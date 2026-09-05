# PostgreSQL 数据库说明

本机维护的 PostgreSQL 数据库,服务于 iGEM platform。

## 连接

| 项 | 值 |
|---|---|
| 数据库 | `igem_peptides` |
| 用户 | `igem` |
| 主机 / 端口 | `127.0.0.1:5432` |
| 密码 | `igem_local_2026` |

命令行:

```bash
PGPASSWORD=igem_local_2026 psql -h 127.0.0.1 -U igem -d igem_peptides
```

应用代码通过 `IGEM_PG_DSN` 环境变量读取 DSN,缺失时回落到上述默认连接串:

```bash
export IGEM_PG_DSN="host=127.0.0.1 port=5432 dbname=igem_peptides user=igem password=igem_local_2026"
```

建表脚本位于 `src/setup/sql/`。
