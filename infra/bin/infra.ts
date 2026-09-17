#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { InfraStack } from '../lib/infra-stack';

const app = new cdk.App();

const environment = app.node.tryGetContext('environment') || 'dev';
const region = process.env.CDK_DEFAULT_REGION || 'ap-south-1';
const account = process.env.CDK_DEFAULT_ACCOUNT || process.env.AWS_ACCOUNT_ID;

new InfraStack(app, `EvaluatorStack-${environment}`, {
  environment,
  env: {
    account,
    region,
  },
  description: `Evaluator.ai Stack (${environment}) - AI-powered Indian exam paper evaluation pipeline`,
});
