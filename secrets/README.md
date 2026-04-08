把 SMTP 密码放到这个目录下的 `smtp_password` 文件中。

示例：

```bash
mkdir -p secrets
printf '%s\n' '你的邮箱 app password' > secrets/smtp_password
chmod 600 secrets/smtp_password
```

容器启动后会把该文件挂载到 `/run/secrets/smtp_password`，
应用优先从 `SMTP_PASSWORD_FILE` 读取密码。
