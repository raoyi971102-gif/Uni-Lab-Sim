# GitHub ↔ CNB 双端部署：密钥配置

手动点 GitHub Actions **Run workflow** 时：

1. 部署国外服务器（现有 `SSH_*` secrets，不变）
2. 把当前 `main` 强制推到 [cnb.cool/emoera/PLC-Sim](https://cnb.cool/emoera/PLC-Sim)
3. CNB 流水线（[`.cnb.yml`](../.cnb.yml)）再 rsync 到国内机 `81.69.12.254`

OPC UA 客户端地址仍是 `opc.tcp://81.69.12.254:4855/xuse_sim`（或域名），与部署路径无关。

## 1. GitHub Actions Secrets

仓库：https://github.com/raoyi971102-gif/PLC-Sim/settings/secrets/actions

| Name | 必填 | 说明 |
|---|---|---|
| `CNB_TOKEN` | 是 | cnb.cool 个人访问令牌，需对 `emoera/PLC-Sim` 有写权限 |
| `CNB_USERNAME` | 否 | HTTPS 推送用户名，默认 `cnb`；按 CNB 文档填你的用户名亦可 |

国外部署继续用已有的 `SSH_HOST` / `SSH_USER` / `SSH_KEY`（及可选 `SSH_PORT`）。

## 2. CNB 部署密钥（private 仓）

实际使用的是 **private** 仓 [`emoera/plc-sim-deploy-env`](https://cnb.cool/emoera/plc-sim-deploy-env) 里的 `deploy-cn.yml`（可用 git 推送，避免密钥仓 Web 编辑弄坏私钥换行导致 `error in libcrypto`）。

[`.cnb.yml`](../.cnb.yml) 的 imports：

`https://cnb.cool/emoera/plc-sim-deploy-env/-/blob/main/deploy-cn.yml`

文件内容需含 `LOGIN_USER`、`PRIVATE_KEY`（OpenSSH 私钥全文），以及可选的 `allow_slugs` / `allow_images` / `allow_branches`。

国内机 `authorized_keys` 需包含对应公钥（本机一般为 `~/.ssh/plcsim_deploy.pub`）。

## 3. 部署目标命名

GitHub Actions 与 CNB 现在都把源码同步到 `/www/wwwroot/PLC-Sim`。国内机的
systemd 单元统一命名为 `plcsim-gui`，该单元的 `WorkingDirectory`、`ExecStart`
及相关环境文件也必须指向新目录，并使用 `PLCSIM_*` 配置前缀。首次部署前可执行：

```bash
systemctl cat plcsim-gui
systemctl show -p WorkingDirectory,ExecStart plcsim-gui
```

确认输出没有旧路径后再触发流水线，否则 rsync 虽然成功，健康检查仍会因服务从
错误目录启动而失败。

## 4. 服务器 Python 版本

国内、国外服务器的 `/www/wwwroot/PLC-Sim/.venv` 都必须由 Python 3.11 创建。
部署脚本会先做精确版本检查。发现 3.10、3.12 或其他版本时，如果服务器上
存在 `python3.11`，会先在临时目录创建新环境并安装全部依赖，成功后再原子替换
`.venv`；创建或安装失败不会移动旧环境。旧环境会暂存为
`.venv-before-python311-*`，下一次 rsync 部署时自动清理。

服务器必须预先安装 Python 3.11 及其 venv 模块；如果解释器不在 `PATH`，可在
手动运行迁移脚本时把 `PLCSIM_PYTHON` 设置为它的绝对路径。也可以在维护窗口
中直接迁移：

```bash
cd /www/wwwroot/PLC-Sim
PLCSIM_PYTHON=/opt/python3.11/bin/python3.11 \
  bash scripts/ensure_deploy_venv.sh "$PWD"
```

或者完全手动执行：

```bash
cd /www/wwwroot/PLC-Sim
.venv/bin/python --version
mv .venv .venv-before-python311
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

确认新服务正常后，再自行归档或移除旧环境。

## 5. 验证

1. 推送到 GitHub `main` → 只跑 `test`
2. Actions → **PLC-Sim CI / 手动部署** → **Run workflow**
3. `deploy`（国外）与 `sync_cnb` 都应绿
4. CNB 构建页出现 `main` push 流水线且绿
5. `curl -fsS https://opcua.emoera.cn/api/version` 中 `release` 为新短 SHA
