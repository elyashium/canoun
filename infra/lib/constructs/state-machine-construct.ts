import * as path from 'path';
import { Construct } from 'constructs';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as sfn from 'aws-cdk-lib/aws-stepfunctions';
import * as tasks from 'aws-cdk-lib/aws-stepfunctions-tasks';
import * as cdk from 'aws-cdk-lib';

export interface StateMachineConstructProps {
  vpc: ec2.IVpc;
  lambdaSecurityGroup: ec2.ISecurityGroup;
  rawBookletsBucket: s3.IBucket;
  pageImagesBucket: s3.IBucket;
  evaluationsTable: dynamodb.ITable;
  rubricsTable: dynamodb.ITable;
  embeddingCacheUrl?: string;
  sagemakerEndpointName?: string;
  environment: string;
}

export class StateMachineConstruct extends Construct {
  public readonly stateMachine: sfn.StateMachine;
  public readonly splitterLambda: lambda.IFunction;
  public readonly ocrLambda: lambda.IFunction;
  public readonly aggregatorLambda: lambda.IFunction;
  public readonly scorerLambda: lambda.IFunction;
  public readonly finalizerLambda: lambda.IFunction;
  public readonly failHandlerLambda: lambda.IFunction;

  constructor(scope: Construct, id: string, props: StateMachineConstructProps) {
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

    const commonEnv = {
      RAW_BUCKET_NAME: props.rawBookletsBucket.bucketName,
      PAGE_BUCKET_NAME: props.pageImagesBucket.bucketName,
      EVALUATIONS_TABLE: props.evaluationsTable.tableName,
      RUBRICS_TABLE: props.rubricsTable.tableName,
      BEDROCK_MODEL_ID: 'anthropic.claude-3-5-haiku-20241022-v1:0',
      EMBEDDING_CACHE_URL: props.embeddingCacheUrl || '',
      SAGEMAKER_ENDPOINT_NAME: props.sagemakerEndpointName || '',
    };

    // Shared Bedrock Policy
    const bedrockInvokePolicy = new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel'],
      resources: ['*'],
    });

    // 1. Fail Handler Lambda
    this.failHandlerLambda = new lambda.Function(this, 'FailHandlerFunction', {
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'lambdas.fail_handler.fail_handler.lambda_handler',
      code: lambdaCode,
      memorySize: 256,
      timeout: cdk.Duration.seconds(30),
      vpc: props.vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      securityGroups: [props.lambdaSecurityGroup],
      environment: commonEnv,
    });
    props.evaluationsTable.grantReadWriteData(this.failHandlerLambda);

    // 2. Splitter Lambda
    this.splitterLambda = new lambda.Function(this, 'SplitterFunction', {
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'lambdas.splitter.splitter.lambda_handler',
      code: lambdaCode,
      memorySize: 1024,
      timeout: cdk.Duration.minutes(3),
      vpc: props.vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      securityGroups: [props.lambdaSecurityGroup],
      environment: commonEnv,
    });
    props.rawBookletsBucket.grantRead(this.splitterLambda);
    props.pageImagesBucket.grantReadWrite(this.splitterLambda);
    props.rubricsTable.grantReadData(this.splitterLambda);

    // 3. OCR Worker Lambda
    this.ocrLambda = new lambda.Function(this, 'OcrWorkerFunction', {
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'lambdas.ocr.ocr_worker.lambda_handler',
      code: lambdaCode,
      memorySize: 512,
      timeout: cdk.Duration.minutes(2),
      vpc: props.vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      securityGroups: [props.lambdaSecurityGroup],
      environment: commonEnv,
    });
    props.pageImagesBucket.grantRead(this.ocrLambda);
    this.ocrLambda.addToRolePolicy(bedrockInvokePolicy);

    // 4. Aggregator Lambda
    this.aggregatorLambda = new lambda.Function(this, 'AggregatorFunction', {
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'lambdas.aggregator.aggregator.lambda_handler',
      code: lambdaCode,
      memorySize: 512,
      timeout: cdk.Duration.minutes(1),
      vpc: props.vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      securityGroups: [props.lambdaSecurityGroup],
      environment: commonEnv,
    });
    props.rubricsTable.grantReadData(this.aggregatorLambda);

    // 5. Scoring Worker Lambda
    this.scorerLambda = new lambda.Function(this, 'ScoringWorkerFunction', {
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'lambdas.scorer.scoring_worker.lambda_handler',
      code: lambdaCode,
      memorySize: 512,
      timeout: cdk.Duration.minutes(2),
      vpc: props.vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      securityGroups: [props.lambdaSecurityGroup],
      environment: commonEnv,
    });
    this.scorerLambda.addToRolePolicy(bedrockInvokePolicy);

    // 6. Finalizer Lambda
    this.finalizerLambda = new lambda.Function(this, 'FinalizerFunction', {
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'lambdas.finalizer.finalizer.lambda_handler',
      code: lambdaCode,
      memorySize: 512,
      timeout: cdk.Duration.minutes(2),
      vpc: props.vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      securityGroups: [props.lambdaSecurityGroup],
      environment: commonEnv,
    });
    props.evaluationsTable.grantReadWriteData(this.finalizerLambda);
    props.pageImagesBucket.grantReadWrite(this.finalizerLambda);

    // Step Functions Tasks
    const failTask = new tasks.LambdaInvoke(this, 'ExecuteFailHandler', {
      lambdaFunction: this.failHandlerLambda,
      resultPath: sfn.JsonPath.DISCARD,
    });

    const splitTask = new tasks.LambdaInvoke(this, 'SplitPdfTask', {
      lambdaFunction: this.splitterLambda,
      payloadResponseOnly: true,
    });

    // Retry policy for transient errors, Bedrock 429 throttling, and Lambda concurrency limits
    const bedrockThrottleRetry: sfn.RetryProps = {
      errors: [
        'ThrottlingException',
        'ModelNotReadyException',
        'Bedrock.ThrottlingException',
        'Lambda.TooManyRequestsException',
        'Lambda.ServiceException',
        'Lambda.AWSLambdaException',
      ],
      interval: cdk.Duration.seconds(2),
      maxAttempts: 5,
      backoffRate: 2.0,
      jitterStrategy: sfn.JitterType.FULL,
    };

    const ocrMap = new sfn.Map(this, 'OcrMapState', {
      maxConcurrency: 5,
      itemsPath: sfn.JsonPath.stringAt('$.pages'),
      resultPath: sfn.JsonPath.stringAt('$.ocr_results'),
    });
    const ocrTask = new tasks.LambdaInvoke(this, 'OcrPageTask', {
      lambdaFunction: this.ocrLambda,
      payloadResponseOnly: true,
    });
    ocrTask.addRetry(bedrockThrottleRetry);
    ocrMap.itemProcessor(ocrTask);

    const aggregateTask = new tasks.LambdaInvoke(this, 'AggregateAnswersTask', {
      lambdaFunction: this.aggregatorLambda,
      payloadResponseOnly: true,
    });

    const scoringMap = new sfn.Map(this, 'ScoringMapState', {
      maxConcurrency: 5,
      itemsPath: sfn.JsonPath.stringAt('$.evaluation_items'),
      resultPath: sfn.JsonPath.stringAt('$.scores'),
    });
    const scoringTask = new tasks.LambdaInvoke(this, 'ScoreQuestionTask', {
      lambdaFunction: this.scorerLambda,
      payloadResponseOnly: true,
    });
    scoringTask.addRetry(bedrockThrottleRetry);
    scoringMap.itemProcessor(scoringTask);

    const finalizeTask = new tasks.LambdaInvoke(this, 'FinalizeEvaluationTask', {
      lambdaFunction: this.finalizerLambda,
      payloadResponseOnly: true,
    });

    // Workflow chain with Catch handlers
    splitTask.addCatch(failTask, { resultPath: '$.error' });
    ocrMap.addCatch(failTask, { resultPath: '$.error' });
    aggregateTask.addCatch(failTask, { resultPath: '$.error' });
    scoringMap.addCatch(failTask, { resultPath: '$.error' });
    finalizeTask.addCatch(failTask, { resultPath: '$.error' });

    const definition = splitTask
      .next(ocrMap)
      .next(aggregateTask)
      .next(scoringMap)
      .next(finalizeTask);

    this.stateMachine = new sfn.StateMachine(this, 'EvaluationStateMachine', {
      stateMachineName: `evaluator-pipeline-${props.environment}`,
      definitionBody: sfn.DefinitionBody.fromChainable(definition),
      stateMachineType: sfn.StateMachineType.STANDARD,
      timeout: cdk.Duration.minutes(10),
    });
  }
}
