import { Construct } from 'constructs';
import * as ec2 from 'aws-cdk-lib/aws-ec2';

export interface VpcConstructProps {
  environment: string;
}

export class VpcConstruct extends Construct {
  public readonly vpc: ec2.IVpc;
  public readonly lambdaSecurityGroup: ec2.ISecurityGroup;
  public readonly sidecarSecurityGroup: ec2.ISecurityGroup;
  public readonly endpointSecurityGroup: ec2.ISecurityGroup;

  constructor(scope: Construct, id: string, props: VpcConstructProps) {
    super(scope, id);

    // VPC with 2 AZs and 0 NAT Gateways (eliminates NAT Gateway idle charges)
    this.vpc = new ec2.Vpc(this, 'EvaluatorVpc', {
      maxAzs: 2,
      natGateways: 0,
      subnetConfiguration: [
        {
          name: 'PrivateIsolated',
          subnetType: ec2.SubnetType.PRIVATE_ISOLATED,
          cidrMask: 24,
        },
      ],
    });

    // Security Groups
    this.endpointSecurityGroup = new ec2.SecurityGroup(this, 'EndpointSecurityGroup', {
      vpc: this.vpc,
      description: 'Security group for VPC Interface Endpoints',
      allowAllOutbound: true,
    });

    this.lambdaSecurityGroup = new ec2.SecurityGroup(this, 'LambdaSecurityGroup', {
      vpc: this.vpc,
      description: 'Security group for evaluator backend Lambdas',
      allowAllOutbound: true,
    });

    this.sidecarSecurityGroup = new ec2.SecurityGroup(this, 'SidecarSecurityGroup', {
      vpc: this.vpc,
      description: 'Security group for embedding cache sidecar',
      allowAllOutbound: true,
    });

    // Allow VPC endpoints to accept HTTPS traffic from Lambdas and Sidecars
    this.endpointSecurityGroup.addIngressRule(
      this.lambdaSecurityGroup,
      ec2.Port.tcp(443),
      'Allow Lambdas to reach VPC interface endpoints'
    );
    this.endpointSecurityGroup.addIngressRule(
      this.sidecarSecurityGroup,
      ec2.Port.tcp(443),
      'Allow Sidecar tasks to reach VPC interface endpoints (ECR pull)'
    );

    // Allow Lambdas to communicate with sidecar on port 8000
    this.sidecarSecurityGroup.addIngressRule(
      this.lambdaSecurityGroup,
      ec2.Port.tcp(8000),
      'Allow Lambdas to reach embedding cache on port 8000'
    );

    // Free Gateway VPC Endpoints for S3 (also required for ECR image layer pulls) and DynamoDB
    this.vpc.addGatewayEndpoint('S3GatewayEndpoint', {
      service: ec2.GatewayVpcEndpointAwsService.S3,
    });

    this.vpc.addGatewayEndpoint('DynamoDbGatewayEndpoint', {
      service: ec2.GatewayVpcEndpointAwsService.DYNAMODB,
    });

    // Interface Endpoints to eliminate NAT Gateway for AWS SDK calls & ECS ECR pulls:
    // NOTE: SageMaker runtime endpoint intentionally omitted to prevent idle billing ($0.02/hr).
    const interfaceServices = [
      { name: 'BedrockRuntimeEndpoint', service: ec2.InterfaceVpcEndpointAwsService.BEDROCK_RUNTIME },
      { name: 'EcrApiEndpoint', service: ec2.InterfaceVpcEndpointAwsService.ECR },
      { name: 'EcrDkrEndpoint', service: ec2.InterfaceVpcEndpointAwsService.ECR_DOCKER },
      { name: 'SecretsManagerEndpoint', service: ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER },
      { name: 'SqsEndpoint', service: ec2.InterfaceVpcEndpointAwsService.SQS },
      { name: 'StepFunctionsEndpoint', service: ec2.InterfaceVpcEndpointAwsService.STEP_FUNCTIONS },
      { name: 'CloudWatchLogsEndpoint', service: ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_LOGS },
    ];

    for (const item of interfaceServices) {
      this.vpc.addInterfaceEndpoint(item.name, {
        service: item.service,
        subnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
        securityGroups: [this.endpointSecurityGroup],
      });
    }
  }
}
