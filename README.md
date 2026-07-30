# Rule

自用 Mihomo 自定义分流规则补充库。

本地只维护 YAML（`payload` 格式），推送到 `main` 后，GitHub Actions 会自动转换成同目录同名的 Mihomo `.mrs` 和 sing-box `.srs` 并回写仓库。

## 用法

1. 在仓库任意位置新增/修改规则文件（建议放在 `rules/`）：

```yaml
payload:
  - example.com
  - +.google.com
```

或 IP/CIDR：

```yaml
payload:
  - 1.1.1.1/32
  - 10.0.0.0/8
```

2. 提交并推送到 `main`。
3. 等待 Actions 生成同名 `.mrs` 和 `.srs`（例如 `rules/foo.yaml` → `rules/foo.mrs`、`rules/foo.srs`）。
4. 删除 YAML 时，对应 `.mrs`、`.srs` 会一并删除。

类型自动识别：payload 全是 IP/CIDR 则为 `ipcidr`，否则为 `domain`。不要在同一个文件里混装。

## 在 Mihomo 里引用

把下面的链接换成你的文件路径：

```yaml
rule-providers:
  my-ads:
    type: http
    behavior: domain
    format: mrs
    url: https://raw.githubusercontent.com/FateLightX/Rule/main/rules/example-ads.mrs
    path: ./ruleset/example-ads.mrs
    interval: 86400

  my-lan-ip:
    type: http
    behavior: ipcidr
    format: mrs
    url: https://raw.githubusercontent.com/FateLightX/Rule/main/rules/example-lan-ip.mrs
    path: ./ruleset/example-lan-ip.mrs
    interval: 86400

rules:
  - RULE-SET,my-ads,REJECT
  - RULE-SET,my-lan-ip,DIRECT
```

也可使用 jsDelivr：

```text
https://cdn.jsdelivr.net/gh/FateLightX/Rule@main/rules/example-ads.mrs
```

## 在 sing-box 里引用

远程规则集使用同名 `.srs`：

```json
{
  "route": {
    "rule_set": [
      {
        "tag": "my-ads",
        "type": "remote",
        "format": "binary",
        "url": "https://raw.githubusercontent.com/FateLightX/Rule/main/rules/example-ads.srs"
      }
    ]
  }
}
```

`+.example.com` 会转换为 sing-box `domain_suffix`，普通域名转换为 `domain`，IP/CIDR 转换为 `ip_cidr`。

## 本地手动转换（可选）

```bash
# 需要本机有 mihomo、sing-box，以及: pip install pyyaml
python3 scripts/convert_to_mrs.py \
  --mihomo /path/to/mihomo \
  --sing-box /path/to/sing-box
```

## 约定

| 项 | 行为 |
| --- | --- |
| 触发 | push 到 `main` 且 YAML/脚本变更 |
| 产物 | 与 YAML 同目录、同名 `.mrs` 和 `.srs` |
| 回写 | Actions 直接 commit 回同一分支 |
| 删除 | 删 YAML 时同步删 orphan `.mrs`、`.srs` |
| 格式 | 标准 rule-set `payload` 列表 |
