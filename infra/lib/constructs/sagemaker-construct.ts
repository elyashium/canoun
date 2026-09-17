import { Construct } from 'constructs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as sagemaker from 'aws-cdk-lib/aws-sagemaker';

export interface SageMakerConstructProps {
  asyncResultsBucket: s3.IBucket;
  environment: string;
}

export class SageMakerConstruct extends Construct {
  public readonly endpointName: string;

  constructor(scope: Construct, id: string, props: SageMakerConstructProps) {
    super(scope, id);

    // IAM execution role for SageMaker Async Inference
    const sageMakerRole = new iam.Role(this, 'SageMakerExecutionRole', {
      assumedBy: new iam.ServicePrincipal('sagemaker.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonSageMakerFullAccess'),
      ],
    });

    props.asyncResultsBucket.grantReadWrite(sageMakerRole);

    this.endpointName = `evaluator-calibration-${props.environment}`;
    // SageMaker Async Inference endpoint configuration is dynamically referenced by Lambda
  }
}
