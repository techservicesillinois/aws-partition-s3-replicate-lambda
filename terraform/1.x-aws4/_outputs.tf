output "replicate_user" {
    value = {
        arn       = aws_iam_user.replicate.arn
        name      = aws_iam_user.replicate.name
        unique_id = aws_iam_user.replicate.unique_id
    }
}
