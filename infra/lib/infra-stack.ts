import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { VpcConstruct } from './constructs/vpc-construct';
import { StorageConstruct } from './constructs/storage-construct';
import { QueuesConstruct } from './constructs/queues-construct';
import { EmbeddingCacheConstruct } from './constructs/embedding-cache-construct';
import { SageMakerConstruct } from './constructs/sagemaker-construct';
import { StateMachineConstruct } from './constructs/state-machine-construct';
import { ApiConstruct } from './constructs/api-construct';
import { AlarmsConstruct } from './constructs/alarms-construct';
import { CostConstruct } from './constructs/cost-construct';
import { AuditConstruct } from './constructs/audit-construct';

export interface EvaluatorStackProps extends cdk.StackProps {
  environment: string;
}

export class InfraStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: EvaluatorStackProps) {
    super(scope, id, props);

    const env = props.environment || 'dev';

    // 1. Networking (VPC, 0 NAT Gateways, isolated subnets, endpoints)
    const vpcConstruct = new VpcConstruct(this, 'Vpc', { environment: env });

    // 2. Storage (3 S3 Buckets, 4 DynamoDB Tables)
    const storageConstruct = new StorageConstruct(this, 'Storage', { environment: env });

    // 3. Queues (Trigger SQS queue with DLQ and S3 event notification)
    const queuesConstruct = new QueuesConstruct(this, 'Queues', {
      rawBookletsBucket: storageConstruct.rawBookletsBucket,
    });

    // 4. Embedding Cache Sidecar (ECS Fargate Spot + Internal ALB)
    const embeddingCacheConstruct = new EmbeddingCacheConstruct(this, 'EmbeddingCache', {
      vpc: vpcConstruct.vpc,
      sidecarSecurityGroup: vpcConstruct.sidecarSecurityGroup,
      environment: env,
    });

    // 5. SageMaker Calibration Endpoint
    const sageMakerConstruct = new SageMakerConstruct(this, 'SageMaker', {
      asyncResultsBucket: storageConstruct.asyncResultsBucket,
      environment: env,
    });

    // 6. Step Functions Pipeline (Splitter -> OCR Map -> Aggregator -> Scoring Map -> Finalizer)
    const stateMachineConstruct = new StateMachineConstruct(this, 'StateMachine', {
      vpc: vpcConstruct.vpc,
      lambdaSecurityGroup: vpcConstruct.lambdaSecurityGroup,
      rawBookletsBucket: storageConstruct.rawBookletsBucket,
      pageImagesBucket: storageConstruct.pageImagesBucket,
      evaluationsTable: storageConstruct.evaluationsTable,
      rubricsTable: storageConstruct.rubricsTable,
      embeddingCacheUrl: embeddingCacheConstruct.serviceUrl,
      sagemakerEndpointName: sageMakerConstruct.endpointName,
      environment: env,
    });

    // 7. API Gateway & Cognito Auth & Trigger Lambda
    const apiConstruct = new ApiConstruct(this, 'Api', {
      vpc: vpcConstruct.vpc,
      lambdaSecurityGroup: vpcConstruct.lambdaSecurityGroup,
      rawBookletsBucket: storageConstruct.rawBookletsBucket,
      triggerQueue: queuesConstruct.triggerQueue,
      evaluationsTable: storageConstruct.evaluationsTable,
      idempotencyTable: storageConstruct.idempotencyTable,
      rubricsTable: storageConstruct.rubricsTable,
      reviewerGrantsTable: storageConstruct.reviewerGrantsTable,
      stateMachine: stateMachineConstruct.stateMachine,
      environment: env,
    });

    // 8. CloudWatch Alarms & SNS Alerting
    const alarmsConstruct = new AlarmsConstruct(this, 'Alarms', {
      stateMachine: stateMachineConstruct.stateMachine,
      triggerDlq: queuesConstruct.triggerDlq,
      httpApi: apiConstruct.httpApi,
      splitterLambda: stateMachineConstruct.splitterLambda,
      scorerLambda: stateMachineConstruct.scorerLambda,
      environment: env,
    });

    // 9. Cost Guardrails ($100 AWS Budget & notifications)
    new CostConstruct(this, 'CostBudget', {
      alertTopic: alarmsConstruct.alertTopic,
      environment: env,
    });

    // 10. Audit Logging (CloudTrail)
    new AuditConstruct(this, 'Audit', { environment: env });

    // Stack Outputs
    new cdk.CfnOutput(this, 'ApiUrl', {
      value: apiConstruct.apiEndpoint,
      description: 'HTTP API Gateway URL',
      exportName: `EvaluatorApiUrl-${env}`,
    });

    new cdk.CfnOutput(this, 'UserPoolId', {
      value: apiConstruct.userPool.userPoolId,
      description: 'Cognito User Pool ID',
      exportName: `EvaluatorUserPoolId-${env}`,
    });

    new cdk.CfnOutput(this, 'UserPoolClientId', {
      value: apiConstruct.userPoolClient.userPoolClientId,
      description: 'Cognito User Pool Client ID',
      exportName: `EvaluatorUserPoolClientId-${env}`,
    });

    new cdk.CfnOutput(this, 'RawBookletsBucketName', {
      value: storageConstruct.rawBookletsBucket.bucketName,
      description: 'S3 Raw Uploads Bucket',
      exportName: `EvaluatorRawBucket-${env}`,
    });

    new cdk.CfnOutput(this, 'StateMachineArn', {
      value: stateMachineConstruct.stateMachine.stateMachineArn,
      description: 'Step Functions Pipeline ARN',
      exportName: `EvaluatorStateMachineArn-${env}`,
    });

    new cdk.CfnOutput(this, 'EmbeddingCacheServiceUrl', {
      value: embeddingCacheConstruct.serviceUrl,
      description: 'Cloud Map Private DNS Endpoint for Embedding Cache Sidecar',
      exportName: `EvaluatorEmbeddingCacheUrl-${env}`,
    });
  }
}
