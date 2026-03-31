"""Deploy to ECS Express Mode using boto3."""
import boto3
import json

client = boto3.client("ecs", region_name="us-east-1")

response = client.create_express_gateway_service(
    executionRoleArn="arn:aws:iam::377693677956:role/ecsTaskExecutionRole",
    infrastructureRoleArn="arn:aws:iam::377693677956:role/ecsInfrastructureRoleForExpressServices",
    serviceName="aem-kb-system",
    cpu="1024",
    memory="2048",
    healthCheckPath="/health",
    scalingTarget={"minTaskCount": 1, "maxTaskCount": 1},
    primaryContainer={
        "image": "377693677956.dkr.ecr.us-east-1.amazonaws.com/aem-kb-system:deploy-20260331-0900",
        "containerPort": 80,
        "environment": [
            {"name": "DATABASE_URL", "value": "postgresql+asyncpg://neondb_owner:npg_qi7Ikv3EZPAX@ep-delicate-haze-ai7j6j3u-pooler.c-4.us-east-1.aws.neon.tech/neondb?ssl=require"},
            {"name": "AWS_REGION", "value": "us-east-1"},
            {"name": "S3_BUCKET_NAME", "value": "s3-customer-success-kb"},
            {"name": "BEDROCK_MODEL_ID", "value": "us.anthropic.claude-sonnet-4-20250514-v1:0"},
            {"name": "BEDROCK_KB_ID", "value": "JQQGZFUZLZ"},
            {"name": "BEDROCK_MAX_TOKENS", "value": "16000"},
            {"name": "AEM_REQUEST_TIMEOUT", "value": "30"},
            {"name": "AUTO_APPROVE_THRESHOLD", "value": "0.7"},
            {"name": "AUTO_REJECT_THRESHOLD", "value": "0.2"},
            {"name": "ALLOWLIST", "value": '["*/accordionitem","*/text","*/richtext","*/tabitem","*/termsandconditions","*/policytext","*/contentfragment","*/teaser","*/hero","*/accordion","*/tabs","*/anchorsection","*/herobanner","*/contentcardelement","*/contentcard"]'},
            {"name": "DENYLIST", "value": '["*/responsivegrid","*/container","*/page","*/header","*/footer","*/navigation","*/breadcrumb","*/image","*/button","*/separator","*/spacer","*/experiencefragment","*/languagenavigation","*/search"]'},
        ],
    },
)

print(json.dumps(response, indent=2, default=str))
