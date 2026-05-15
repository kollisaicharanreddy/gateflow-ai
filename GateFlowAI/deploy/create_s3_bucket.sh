#!/bin/bash
# create_s3_bucket.sh — Run ONCE from your local machine (needs AWS CLI)
# Creates the S3 bucket and blocks all public access (files served via presigned URLs only)

BUCKET=gateflow-uploads
REGION=ap-south-2

echo "=== Creating S3 bucket: $BUCKET in $REGION ==="
aws s3api create-bucket \
  --bucket $BUCKET \
  --region $REGION \
  --create-bucket-configuration LocationConstraint=$REGION

echo "=== Blocking all public access ==="
aws s3api put-public-access-block \
  --bucket $BUCKET \
  --public-access-block-configuration \
    "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

echo "=== Done. Bucket $BUCKET is private. Files served via presigned URLs. ==="
