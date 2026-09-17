import { Construct } from 'constructs';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3n from 'aws-cdk-lib/aws-s3-notifications';
import * as cdk from 'aws-cdk-lib';

export interface QueuesConstructProps {
  rawBookletsBucket: s3.IBucket;
}

export class QueuesConstruct extends Construct {
  public readonly triggerQueue: sqs.IQueue;
  public readonly triggerDlq: sqs.IQueue;

  constructor(scope: Construct, id: string, props: QueuesConstructProps) {
    super(scope, id);

    // Dead Letter Queue
    this.triggerDlq = new sqs.Queue(this, 'TriggerDlq', {
      queueName: 'evaluator-trigger-dlq',
      retentionPeriod: cdk.Duration.days(14),
      enforceSSL: true,
    });

    // SQS Trigger Queue
    this.triggerQueue = new sqs.Queue(this, 'TriggerQueue', {
      queueName: 'evaluator-trigger-queue',
      visibilityTimeout: cdk.Duration.minutes(5),
      retentionPeriod: cdk.Duration.days(4),
      enforceSSL: true,
      deadLetterQueue: {
        maxReceiveCount: 3,
        queue: this.triggerDlq,
      },
    });

    // Notify SQS queue when raw answer booklet upload is completed
    props.rawBookletsBucket.addEventNotification(
      s3.EventType.OBJECT_CREATED,
      new s3n.SqsDestination(this.triggerQueue),
      { prefix: 'uploads/', suffix: '.pdf' }
    );
  }
}
