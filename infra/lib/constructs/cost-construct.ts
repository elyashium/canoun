import { Construct } from 'constructs';
import * as budgets from 'aws-cdk-lib/aws-budgets';
import * as sns from 'aws-cdk-lib/aws-sns';

export interface CostConstructProps {
  alertTopic: sns.ITopic;
  environment: string;
}

export class CostConstruct extends Construct {
  constructor(scope: Construct, id: string, props: CostConstructProps) {
    super(scope, id);

    // $100 AWS Credit Budget with notifications at 80% and 100%
    new budgets.CfnBudget(this, 'CreditLimitBudget', {
      budget: {
        budgetName: `EvaluatorAI-CreditLimit-${props.environment}`,
        budgetType: 'COST',
        timeUnit: 'MONTHLY',
        budgetLimit: {
          amount: 100,
          unit: 'USD',
        },
      },
      notificationsWithSubscribers: [
        {
          notification: {
            comparisonOperator: 'GREATER_THAN',
            notificationType: 'ACTUAL',
            threshold: 80,
            thresholdType: 'PERCENTAGE',
          },
          subscribers: [
            {
              subscriptionType: 'SNS',
              address: props.alertTopic.topicArn,
            },
          ],
        },
        {
          notification: {
            comparisonOperator: 'GREATER_THAN',
            notificationType: 'ACTUAL',
            threshold: 100,
            thresholdType: 'PERCENTAGE',
          },
          subscribers: [
            {
              subscriptionType: 'SNS',
              address: props.alertTopic.topicArn,
            },
          ],
        },
      ],
    });
  }
}
