import * as path from 'path';
import { Construct } from 'constructs';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as servicediscovery from 'aws-cdk-lib/aws-servicediscovery';
import * as cdk from 'aws-cdk-lib';

export interface EmbeddingCacheConstructProps {
  vpc: ec2.IVpc;
  sidecarSecurityGroup: ec2.ISecurityGroup;
  environment: string;
}

export class EmbeddingCacheConstruct extends Construct {
  public readonly serviceUrl: string;
  public readonly fargateService: ecs.FargateService;

  constructor(scope: Construct, id: string, props: EmbeddingCacheConstructProps) {
    super(scope, id);

    // Private Cloud Map DNS namespace inside the VPC (eliminates ~$16+/mo ALB baseline charges)
    const namespace = new servicediscovery.PrivateDnsNamespace(this, 'ServiceNamespace', {
      name: 'evaluator.local',
      vpc: props.vpc,
      description: 'Private DNS namespace for evaluator internal microservices',
    });

    const cluster = new ecs.Cluster(this, 'EvaluatorCluster', {
      vpc: props.vpc,
      enableFargateCapacityProviders: true,
      defaultCloudMapNamespace: {
        name: 'evaluator.local',
      },
    });

    // Fargate Task Definition (0.5 vCPU, 1 GB RAM)
    const taskDefinition = new ecs.FargateTaskDefinition(this, 'EmbeddingSidecarTask', {
      cpu: 512,
      memoryLimitMiB: 1024,
    });

    taskDefinition.addContainer('EmbeddingContainer', {
      image: ecs.ContainerImage.fromAsset(path.join(__dirname, '../../../embedding_cache')),
      logging: ecs.LogDrivers.awsLogs({
        streamPrefix: 'embedding-cache',
        logRetention: logs.RetentionDays.ONE_WEEK,
      }),
      portMappings: [{ containerPort: 8000 }],
      environment: {
        PORT: '8000',
      },
    });

    // Deploy on Fargate Spot (70% discount) with Cloud Map private DNS registration
    this.fargateService = new ecs.FargateService(this, 'EmbeddingFargateService', {
      cluster,
      taskDefinition,
      desiredCount: 1,
      minHealthyPercent: 0,
      circuitBreaker: { rollback: true },
      capacityProviderStrategies: [
        {
          capacityProvider: 'FARGATE_SPOT',
          weight: 1,
        },
      ],
      cloudMapOptions: {
        name: 'embedding-cache',
        cloudMapNamespace: namespace,
        dnsRecordType: servicediscovery.DnsRecordType.A,
        dnsTtl: cdk.Duration.seconds(10),
      },
      securityGroups: [props.sidecarSecurityGroup],
      assignPublicIp: false,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
    });

    // Internal direct DNS resolution endpoint without ALB
    this.serviceUrl = 'http://embedding-cache.evaluator.local:8000';
  }
}
