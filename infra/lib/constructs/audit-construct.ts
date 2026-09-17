import { Construct } from 'constructs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as cloudtrail from 'aws-cdk-lib/aws-cloudtrail';
import * as cdk from 'aws-cdk-lib';

export interface AuditConstructProps {
  environment: string;
}

export class AuditConstruct extends Construct {
  public readonly auditBucket: s3.IBucket;
  public readonly trail: cloudtrail.Trail;

  constructor(scope: Construct, id: string, props: AuditConstructProps) {
    super(scope, id);

    const isProd = props.environment === 'prod';

    this.auditBucket = new s3.Bucket(this, 'AuditTrailBucket', {
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      removalPolicy: isProd ? cdk.RemovalPolicy.RETAIN : cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: !isProd,
      lifecycleRules: [
        {
          id: 'TransitionToGlacierOrExpire',
          expiration: cdk.Duration.days(90),
        },
      ],
    });

    this.trail = new cloudtrail.Trail(this, 'EvaluatorAuditTrail', {
      trailName: `evaluator-audit-trail-${props.environment}`,
      bucket: this.auditBucket,
      isMultiRegionTrail: false,
      includeGlobalServiceEvents: true,
      enableFileValidation: true,
    });
  }
}
