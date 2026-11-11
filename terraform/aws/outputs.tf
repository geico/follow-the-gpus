output "bucket_name" {
  description = "Name of the data bucket."
  value       = aws_s3_bucket.data.bucket
}

output "store_url" {
  description = "Store URL of the prober, for the --store flag and the dashboard."
  value       = "s3://${aws_s3_bucket.data.bucket}/"
}

output "instance_id" {
  description = "ID of the prober VM."
  value       = aws_instance.prober.id
}

output "region" {
  description = "AWS Region of the stack."
  value       = var.region
}

output "experiment" {
  description = "Experiment name of the targets file that the stack reads."
  value       = local.experiment
}

output "target_regions" {
  description = "Sorted Regions that the aws section of the targets file names: jobs[].regions, provisioning_probes[].region, and quota.regions."
  # A null in the file (an empty key) counts as an empty list or no Region; flatten([x]) turns a null into one null element that the filters drop;
  # `check-config` rejects the file, but the apply runs before it.
  value = sort(distinct([for r in flatten([
    [for j in flatten([try(local.targets.aws.jobs, null)]) : flatten([try(j.regions, null)]) if j != null],
    [for p in flatten([try(local.targets.aws.provisioning_probes, null)]) : flatten([try(p.region, null)]) if p != null],
    flatten([try(local.targets.aws.quota.regions, null)]),
  ]) : r if r != null]))
}

output "sweeper_function_name" {
  description = "Name of the sweeper Lambda function (spec 0013)."
  value       = aws_lambda_function.sweeper.function_name
}

output "sweeper_stopped_alarm_name" {
  description = "Name of the sweeper-stopped CloudWatch alarm; null without alert_email (spec 0004 section 4.8)."
  value       = one(aws_cloudwatch_metric_alarm.sweeper_stopped[*].alarm_name)
}
