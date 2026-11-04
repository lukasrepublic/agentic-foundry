# Retention checks (FG-DATA-002)

Declare `retention_days` per store in the classification file and keep it at or below the class maximum.
The match between retention and class is declared and reviewed. Platform checks prove only that a retention
exists; they do not know the class.

Where the platform supports it, checkov covers the existence. Typical ids (verify against the pinned checkov
index before relying on them):

- `CKV_AWS_66`: CloudWatch log group has a retention period set.
- `CKV_AWS_338`: CloudWatch log group retention is at least one year (relax per class in a checkov config).
- `CKV2_AWS_61`: S3 bucket has a lifecycle configuration.

```
checkov -d infra/ --check CKV_AWS_66,CKV2_AWS_61
```

Stores on platforms without such a check are declared and reviewed (FG-DATA-003 lens).
