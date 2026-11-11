# The IAM role of the prober VM. Least privilege: 3 read-only EC2 actions, write to the bucket, and
# read of the code zips. ADR 0025 adds read of this experiment's objects and list of the bucket.
# var.grant_billable_provisioning adds the aws_odcr actions (spec 0002 section 4.9): create, cancel,
# describe capacity reservations, and tag on create, each scoped with a tag condition where the API
# supports one. The targets file's own per-job `allow_billable_provisioning` key is a separate
# control the prober's own config check enforces (ADR 0015); Terraform does not read the file for
# consent.

data "aws_caller_identity" "current" {}

locals {
  # The Regions of the aws.provisioning_probes entries. The create, tag, and cancel statements
  # allow only these Regions (aws:RequestedRegion, a global condition key):
  # https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_condition-keys.html#condition-keys-requestedregion
  provisioning_regions = sort(distinct([for r in flatten([for p in flatten([try(local.targets.aws.provisioning_probes, null)]) : flatten([try(p.region, null)]) if p != null]) : r if r != null]))
}

resource "aws_iam_role" "prober" {
  name = "${var.name_prefix}-prober"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "prober" {
  name = "${var.name_prefix}-prober"
  role = aws_iam_role.prober.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [
        {
          Sid    = "ReadCapacitySignals"
          Effect = "Allow"
          Action = [
            "ec2:DescribeCapacityBlockOfferings",
            "ec2:GetSpotPlacementScores",
            "ec2:DescribeSpotPriceHistory",
          ]
          Resource = "*"
        },
        {
          # The resource is the experiment prefix only. It covers the rows and the logs prefix
          # (experiment=<name>/logs/, spec 0003 section 8.2), so the prober needs no separate
          # grant to write its own log objects next to its rows. It covers nothing else in the
          # bucket, such as code/* or manifest.json.
          Sid      = "WriteRecords"
          Effect   = "Allow"
          Action   = "s3:PutObject"
          Resource = "${aws_s3_bucket.data.arn}/experiment=${local.experiment}/*"
        },
        {
          Sid    = "ReadCode"
          Effect = "Allow"
          Action = "s3:GetObject"
          # ADR 0012 section 5: the manifest adds 1 object to this read grant. sync.sh (the VM
          # pull-and-restart script) is hash-named under code/, so the existing code/* grant
          # already covers it; it needs no entry of its own.
          Resource = [
            "${aws_s3_bucket.data.arn}/code/*",
            "${aws_s3_bucket.data.arn}/manifest.json",
          ]
        },
        {
          # ADR 0012 section 5: the VM-side sync timer installs and runs whatever these keys
          # name. WriteRecords is now limited to the experiment prefix, so it no longer matches
          # them; this explicit Deny stays as a second guard that holds if the layout changes.
          # An explicit Deny wins over any Allow in the same policy.
          Sid    = "DenyWriteCode"
          Effect = "Deny"
          Action = ["s3:PutObject", "s3:DeleteObject"]
          Resource = [
            "${aws_s3_bucket.data.arn}/manifest.json",
            "${aws_s3_bucket.data.arn}/code/*",
          ]
        },
        {
          # ADR 0025: `ftg-prober compact` on the VM reads the rows of this experiment and lists
          # the bucket. Its writes use WriteRecords above; the job writes only under
          # experiment=<name>/daily/v1/, a new path, so the probe data stays as it is.
          # https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListObjectsV2.html
          Sid      = "ReadRowsForCompaction"
          Effect   = "Allow"
          Action   = "s3:GetObject"
          Resource = "${aws_s3_bucket.data.arn}/experiment=${local.experiment}/*"
        },
        {
          Sid      = "ListBucketForCompaction"
          Effect   = "Allow"
          Action   = "s3:ListBucket"
          Resource = aws_s3_bucket.data.arn
        },
      ],
      # The create, tag, and cancel statements need at least 1 Region: IAM may reject an empty
      # aws:RequestedRegion list, and no aws.provisioning_probes entry means no right to create,
      # tag, or cancel (spec 0004 section 4.3).
      [for s in [
        {
          # Create a reservation tagged with our experiment tag. aws:RequestTag is the
          # documented global condition key for a create action whose request can carry tags
          # (CreateCapacityReservation takes TagSpecifications):
          # https://docs.aws.amazon.com/IAM/latest/UserGuide/access_tags.html#access_tags_control-tag-keys
          # ec2:EndDateType enforces the EndDate backstop of spec 0002 section 4.9 at the
          # policy level, not only in the probe's own request: a reservation this role
          # creates is always `limited`, never open-ended.
          # https://servicereference.us-east-1.amazonaws.com/v1/ec2/ec2.json
          Sid    = "CreateAllocationProbeReservation"
          Effect = "Allow"
          Action = [
            "ec2:CreateCapacityReservation",
          ]
          Resource = "arn:aws:ec2:*:${data.aws_caller_identity.current.account_id}:capacity-reservation/*"
          Condition = {
            StringEquals = {
              "aws:RequestTag/experiment" = local.experiment
              "ec2:EndDateType"           = "limited"
              "aws:RequestedRegion"       = local.provisioning_regions
            }
          }
        },
        {
          # CreateTags is a separate action from CreateCapacityReservation, and the EC2 service
          # reference lists ec2:CreateAction as one of its own condition keys (not one of
          # CreateCapacityReservation's), so the restriction to "only as part of a
          # CreateCapacityReservation call" must be its own statement:
          # https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/supported-iam-actions-tagging.html
          Sid    = "TagAllocationProbeReservationOnCreate"
          Effect = "Allow"
          Action = [
            "ec2:CreateTags",
          ]
          Resource = "arn:aws:ec2:*:${data.aws_caller_identity.current.account_id}:capacity-reservation/*"
          Condition = {
            StringEquals = {
              "ec2:CreateAction"          = "CreateCapacityReservation"
              "aws:RequestTag/experiment" = local.experiment
              "aws:RequestedRegion"       = local.provisioning_regions
            }
          }
        },
        {
          # Cancel only a reservation already tagged with our experiment tag. aws:ResourceTag
          # is the documented condition key for an action on an existing tagged resource:
          # https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/supported-iam-actions-tagging.html
          Sid    = "CancelAllocationProbeReservation"
          Effect = "Allow"
          Action = [
            "ec2:CancelCapacityReservation",
          ]
          Resource = "arn:aws:ec2:*:${data.aws_caller_identity.current.account_id}:capacity-reservation/*"
          Condition = {
            StringEquals = {
              "aws:ResourceTag/experiment" = local.experiment
              "aws:RequestedRegion"        = local.provisioning_regions
            }
          }
        },
      ] : s if var.grant_billable_provisioning && length(local.provisioning_regions) > 0],
      [for s in [
        {
          # Read-only; DescribeCapacityReservations, DescribeInstanceTypeOfferings, and
          # DescribeAvailabilityZones (zone ID to zone name) support
          # no resource-level permissions, so the resource is "*" (the existing EC2 reads above
          # use the same pattern) and there is no tag condition to apply.
          Sid    = "ReadAllocationProbeCapacity"
          Effect = "Allow"
          Action = [
            "ec2:DescribeAvailabilityZones",
            "ec2:DescribeCapacityReservations",
            "ec2:DescribeInstanceTypeOfferings",
            "ec2:DescribeInstanceTypes",
          ]
          Resource = "*"
        },
        {
          # The probe caps N at floor(On-Demand vCPU quota / vCPUs per instance) before the
          # first attempt (spec 0002 section 4.9): GetServiceQuota for the quota and
          # ec2:DescribeInstanceTypes (above) for the vCPUs. Read-only; GetServiceQuota lists
          # no resource type in the service reference, so the resource is "*":
          # https://docs.aws.amazon.com/service-authorization/latest/reference/list_servicequotas.html
          Sid    = "ReadAllocationProbeQuota"
          Effect = "Allow"
          Action = [
            "servicequotas:GetServiceQuota",
          ]
          Resource = "*"
        },
      ] : s if var.grant_billable_provisioning]
    )
  })
}

# Optional: SSM Session Manager gives a shell with no inbound port and no SSH key.
resource "aws_iam_role_policy_attachment" "ssm" {
  count = var.enable_ssm ? 1 : 0

  role       = aws_iam_role.prober.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "prober" {
  name = "${var.name_prefix}-prober"
  role = aws_iam_role.prober.name
}
