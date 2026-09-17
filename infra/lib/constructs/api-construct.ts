import * as path from 'path';
import { Construct } from 'constructs';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as lambdaEventSources from 'aws-cdk-lib/aws-lambda-event-sources';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as sfn from 'aws-cdk-lib/aws-stepfunctions';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as apigwv2 from 'aws-cdk-lib/aws-apigatewayv2';
import * as apigwv2Integrations from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import * as apigwv2Authorizers from 'aws-cdk-lib/aws-apigatewayv2-authorizers';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as cdk from 'aws-cdk-lib';

export interface ApiConstructProps {
  vpc: ec2.IVpc;
  lambdaSecurityGroup: ec2.ISecurityGroup;
  rawBookletsBucket: s3.IBucket;
  triggerQueue: sqs.IQueue;
  evaluationsTable: dynamodb.ITable;
  idempotencyTable: dynamodb.ITable;
  rubricsTable: dynamodb.ITable;
  reviewerGrantsTable: dynamodb.ITable;
  stateMachine: sfn.StateMachine;
  environment: string;
}

export class ApiConstruct extends Construct {
  public readonly httpApi: apigwv2.HttpApi;
  public readonly apiEndpoint: string;
  public readonly userPool: cognito.UserPool;
  public readonly userPoolClient: cognito.UserPoolClient;

  constructor(scope: Construct, id: string, props: ApiConstructProps) {
    super(scope, id);

    const projectRoot = path.join(__dirname, '../../..');
    const lambdaCode = lambda.Code.fromAsset(projectRoot, {
      exclude: [
        'infra',
        '.git',
        'node_modules',
        '**/node_modules/**',
        '**/*.pyc',
        '**/__pycache__/**',
        'test_images',
        '.venv',
        'temp_uploads',
      ],
    });

    // 1. Cognito User Pool
    this.userPool = new cognito.UserPool(this, 'TeacherUserPool', {
      userPoolName: `evaluator-teachers-${props.environment}`,
      selfSignUpEnabled: true,
      signInAliases: { email: true, username: true },
      autoVerify: { email: true },
      passwordPolicy: {
        minLength: 8,
        requireLowercase: true,
        requireUppercase: true,
        requireDigits: true,
      },
      removalPolicy: props.environment === 'prod' ? cdk.RemovalPolicy.RETAIN : cdk.RemovalPolicy.DESTROY,
    });

    this.userPoolClient = new cognito.UserPoolClient(this, 'TeacherUserPoolClient', {
      userPool: this.userPool,
      generateSecret: false,
      authFlows: {
        userPassword: true,
        userSrp: true,
      },
    });

    // 2. ApiHandler Lambda
    const apiHandlerLambda = new lambda.Function(this, 'ApiHandlerFunction', {
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'lambdas.api_handler.handler.lambda_handler',
      code: lambdaCode,
      memorySize: 512,
      timeout: cdk.Duration.seconds(30),
      vpc: props.vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      securityGroups: [props.lambdaSecurityGroup],
      environment: {
        EVALUATIONS_TABLE: props.evaluationsTable.tableName,
        IDEMPOTENCY_TABLE: props.idempotencyTable.tableName,
        REVIEWER_GRANTS_TABLE: props.reviewerGrantsTable.tableName,
        RUBRICS_TABLE: props.rubricsTable.tableName,
        RAW_BUCKET_NAME: props.rawBookletsBucket.bucketName,
      },
    });

    props.evaluationsTable.grantReadWriteData(apiHandlerLambda);
    props.idempotencyTable.grantReadWriteData(apiHandlerLambda);
    props.reviewerGrantsTable.grantReadWriteData(apiHandlerLambda);
    props.rubricsTable.grantReadData(apiHandlerLambda);
    props.rawBookletsBucket.grantReadWrite(apiHandlerLambda);

    // 3. TriggerHandler Lambda (SQS event consumer -> starts Step Functions)
    const triggerHandlerLambda = new lambda.Function(this, 'TriggerHandlerFunction', {
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'lambdas.trigger_handler.trigger_handler.lambda_handler',
      code: lambdaCode,
      memorySize: 256,
      timeout: cdk.Duration.minutes(1),
      vpc: props.vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      securityGroups: [props.lambdaSecurityGroup],
      environment: {
        STATE_MACHINE_ARN: props.stateMachine.stateMachineArn,
        EVALUATIONS_TABLE: props.evaluationsTable.tableName,
      },
    });

    triggerHandlerLambda.addEventSource(
      new lambdaEventSources.SqsEventSource(props.triggerQueue, {
        batchSize: 5,
        maxBatchingWindow: cdk.Duration.seconds(5),
      })
    );

    props.rawBookletsBucket.grantRead(triggerHandlerLambda);
    props.evaluationsTable.grantReadWriteData(triggerHandlerLambda);
    props.stateMachine.grantStartExecution(triggerHandlerLambda);

    // 4. CloudWatch Access Log Group for HTTP API
    const apiLogGroup = new logs.LogGroup(this, 'ApiAccessLogs', {
      logGroupName: `/aws/http-api/evaluator-${props.environment}`,
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const allowedOrigins = props.environment === 'prod'
      ? ['https://evaluator.ai']
      : [
          'http://localhost:3000',
          'http://localhost:5173',
          'http://127.0.0.1:3000',
          'http://127.0.0.1:5173',
          'https://evaluator.ai',
        ];

    // 5. HTTP API Gateway with CORS & Default Stage Throttling
    this.httpApi = new apigwv2.HttpApi(this, 'EvaluatorHttpApi', {
      apiName: `evaluator-api-${props.environment}`,
      createDefaultStage: false,
      corsPreflight: {
        allowOrigins: allowedOrigins,
        allowMethods: [
          apigwv2.CorsHttpMethod.GET,
          apigwv2.CorsHttpMethod.POST,
          apigwv2.CorsHttpMethod.OPTIONS,
        ],
        allowHeaders: ['Content-Type', 'Authorization', 'Idempotency-Key'],
        maxAge: cdk.Duration.days(1),
      },
    });

    // Explicit $default stage with throttling (100 rps / 200 burst) to prevent Bedrock cost spikes
    // while comfortably handling concurrent demo judges polling GET /api/jobs/{id}
    new apigwv2.HttpStage(this, 'DefaultStage', {
      httpApi: this.httpApi,
      stageName: '$default',
      autoDeploy: true,
      throttle: {
        burstLimit: 200,
        rateLimit: 100,
      },
      accessLogSettings: {
        destination: new apigwv2.LogGroupLogDestination(apiLogGroup),
      },
    });

    const jwtAuthorizer = new apigwv2Authorizers.HttpUserPoolAuthorizer(
      'TeacherAuthorizer',
      this.userPool,
      {
        userPoolClients: [this.userPoolClient],
      }
    );

    const apiIntegration = new apigwv2Integrations.HttpLambdaIntegration(
      'ApiLambdaIntegration',
      apiHandlerLambda
    );

    // Routes
    this.httpApi.addRoutes({
      path: '/api/jobs',
      methods: [apigwv2.HttpMethod.POST],
      integration: apiIntegration,
      authorizer: jwtAuthorizer,
    });

    this.httpApi.addRoutes({
      path: '/api/jobs/{job_id}',
      methods: [apigwv2.HttpMethod.GET],
      integration: apiIntegration,
      authorizer: jwtAuthorizer,
    });

    this.httpApi.addRoutes({
      path: '/api/jobs/{job_id}/grant',
      methods: [apigwv2.HttpMethod.POST],
      integration: apiIntegration,
      authorizer: jwtAuthorizer,
    });

    this.apiEndpoint = this.httpApi.url || '';
  }
}
