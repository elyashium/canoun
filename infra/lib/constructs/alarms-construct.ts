import { Construct } from 'constructs';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as cloudwatchActions from 'aws-cdk-lib/aws-cloudwatch-actions';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import * as sfn from 'aws-cdk-lib/aws-stepfunctions';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as apigwv2 from 'aws-cdk-lib/aws-apigatewayv2';
import * as cdk from 'aws-cdk-lib';

export interface AlarmsConstructProps {
  stateMachine: sfn.StateMachine;
  triggerDlq: sqs.IQueue;
  httpApi: apigwv2.HttpApi;
  splitterLambda: lambda.IFunction;
  scorerLambda: lambda.IFunction;
  environment: string;
}

export class AlarmsConstruct extends Construct {
  public readonly alertTopic: sns.ITopic;

  constructor(scope: Construct, id: string, props: AlarmsConstructProps) {
    super(scope, id);

    this.alertTopic = new sns.Topic(this, 'EvaluatorAlertsTopic', {
      displayName: `Evaluator Pipeline Alerts (${props.environment})`,
    });

    const snsAction = new cloudwatchActions.SnsAction(this.alertTopic);

    // 1. SFN Failed Executions Alarm
    const sfnFailedAlarm = new cloudwatch.Alarm(this, 'SfnFailedAlarm', {
      metric: props.stateMachine.metricFailed({
        period: cdk.Duration.minutes(1),
        statistic: 'Sum',
      }),
      threshold: 1,
      evaluationPeriods: 1,
      alarmDescription: 'Step Function execution failed',
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    sfnFailedAlarm.addAlarmAction(snsAction);

    // 2. DLQ Visible Messages Alarm
    const dlqAlarm = new cloudwatch.Alarm(this, 'DlqMessagesAlarm', {
      metric: props.triggerDlq.metricApproximateNumberOfMessagesVisible({
        period: cdk.Duration.minutes(1),
        statistic: 'Maximum',
      }),
      threshold: 1,
      evaluationPeriods: 1,
      alarmDescription: 'Messages in Trigger DLQ require investigation',
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    dlqAlarm.addAlarmAction(snsAction);

    // 3. API Gateway 5xx Errors Alarm
    const api5xxMetric = new cloudwatch.Metric({
      namespace: 'AWS/ApiGateway',
      metricName: '5XXError',
      dimensionsMap: { ApiId: props.httpApi.apiId },
      period: cdk.Duration.minutes(1),
      statistic: 'Sum',
    });
    const api5xxAlarm = new cloudwatch.Alarm(this, 'Api5xxAlarm', {
      metric: api5xxMetric,
      threshold: 2,
      evaluationPeriods: 1,
      alarmDescription: 'API Gateway 5xx error threshold exceeded',
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    api5xxAlarm.addAlarmAction(snsAction);

    // 4. Splitter Lambda Errors Alarm
    const splitterErrorAlarm = new cloudwatch.Alarm(this, 'SplitterErrorAlarm', {
      metric: props.splitterLambda.metricErrors({
        period: cdk.Duration.minutes(1),
        statistic: 'Sum',
      }),
      threshold: 1,
      evaluationPeriods: 1,
      alarmDescription: 'PDF Splitter Lambda encountered unhandled errors',
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    splitterErrorAlarm.addAlarmAction(snsAction);

    // 5. Scorer Lambda Throttles Alarm
    const scorerThrottleAlarm = new cloudwatch.Alarm(this, 'ScorerThrottleAlarm', {
      metric: props.scorerLambda.metricThrottles({
        period: cdk.Duration.minutes(1),
        statistic: 'Sum',
      }),
      threshold: 1,
      evaluationPeriods: 1,
      alarmDescription: 'Scorer Lambda throttles detected',
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    scorerThrottleAlarm.addAlarmAction(snsAction);
  }
}
