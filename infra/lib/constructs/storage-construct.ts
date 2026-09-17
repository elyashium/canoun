import { Construct } from 'constructs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as cdk from 'aws-cdk-lib';

export interface StorageConstructProps {
  environment: string;
}

export class StorageConstruct extends Construct {
  public readonly rawBookletsBucket: s3.IBucket;
  public readonly pageImagesBucket: s3.IBucket;
  public readonly asyncResultsBucket: s3.IBucket;

  public readonly evaluationsTable: dynamodb.ITable;
  public readonly idempotencyTable: dynamodb.ITable;
  public readonly rubricsTable: dynamodb.ITable;
  public readonly reviewerGrantsTable: dynamodb.ITable;

  constructor(scope: Construct, id: string, props: StorageConstructProps) {
    super(scope, id);

    const isProd = props.environment === 'prod';
    const removalPolicy = isProd ? cdk.RemovalPolicy.RETAIN : cdk.RemovalPolicy.DESTROY;

    // S3: Raw Teacher Uploads
    this.rawBookletsBucket = new s3.Bucket(this, 'RawBookletsBucket', {
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      removalPolicy,
      autoDeleteObjects: !isProd,
      cors: [
        {
          allowedMethods: [s3.HttpMethods.POST, s3.HttpMethods.PUT, s3.HttpMethods.GET],
          allowedOrigins: isProd
            ? ['https://evaluator.ai']
            : [
                'http://localhost:3000',
                'http://localhost:5173',
                'http://127.0.0.1:3000',
                'http://127.0.0.1:5173',
                'https://evaluator.ai',
              ],
          allowedHeaders: ['*'],
          maxAge: 3000,
        },
      ],
      lifecycleRules: [
        {
          id: 'AbortIncompleteMultipart',
          abortIncompleteMultipartUploadAfter: cdk.Duration.days(1),
        },
        {
          id: 'ExpireRawUploads',
          expiration: cdk.Duration.days(30),
        },
      ],
    });

    // S3: Ephemeral Page Images (auto-purged after 1 day)
    this.pageImagesBucket = new s3.Bucket(this, 'PageImagesBucket', {
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      removalPolicy,
      autoDeleteObjects: !isProd,
      lifecycleRules: [
        {
          id: 'ExpireEphemeralPages',
          expiration: cdk.Duration.days(1),
        },
      ],
    });

    // S3: SageMaker Async Inference Results
    this.asyncResultsBucket = new s3.Bucket(this, 'AsyncResultsBucket', {
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      removalPolicy,
      autoDeleteObjects: !isProd,
      lifecycleRules: [
        {
          id: 'ExpireInferenceResults',
          expiration: cdk.Duration.days(7),
        },
      ],
    });

    // DynamoDB: Evaluations Table
    this.evaluationsTable = new dynamodb.Table(this, 'EvaluationsTable', {
      partitionKey: { name: 'job_id', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: isProd },
      removalPolicy,
    });

    // DynamoDB: Idempotency Keys
    this.idempotencyTable = new dynamodb.Table(this, 'IdempotencyTable', {
      partitionKey: { name: 'idempotency_key', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      timeToLiveAttribute: 'ttl',
      removalPolicy,
    });

    // DynamoDB: Rubrics Table
    this.rubricsTable = new dynamodb.Table(this, 'RubricsTable', {
      partitionKey: { name: 'rubric_id', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy,
    });

    // DynamoDB: Reviewer Grants Table (IRR collaboration)
    this.reviewerGrantsTable = new dynamodb.Table(this, 'ReviewerGrantsTable', {
      partitionKey: { name: 'job_id', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'reviewer_id', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      timeToLiveAttribute: 'expires_at',
      removalPolicy,
    });
  }
}
