# 自部署产物分享

把一个项目里的画板、文档或 HTML 页面推送到自己的域名。发布者使用独立令牌，读者用浏览器打开链接。**拿到分享链接的人都可以读取这份产物。** 本服务适合按链接分享的只读快照。

## 在服务器上启动

在服务器上检出本 fork，进入仓库根目录。首次部署生成一个令牌，把它保存在仅自己可读的文件中。

```bash
(umask 077 && set -C && printf 'RYUGI_SELF_HOST_TOKEN=%s\n' "$(openssl rand -hex 32)" > self-host/.env)
docker compose --env-file self-host/.env -f self-host/compose.yaml up -d --build
curl --fail http://127.0.0.1:8787/
```

成功时返回 `Artifact snapshot server`。Compose 默认只监听本机的 8787 端口，可用 `RYUGI_SELF_HOST_PORT` 更改。在自己的域名上配置 HTTPS 反向代理。例如，使用服务器上的 Caddy 时，把域名指向服务器并添加以下站点。

```caddyfile
artifacts.example.com {
    reverse_proxy 127.0.0.1:8787
}
```

SQLite 数据库保存在 Compose 的 `snapshots` 卷中。更新服务时再次运行同一条 `up -d --build` 命令。容器重建和更换发布令牌都会保留已有链接。备份时先 `docker compose --env-file self-host/.env -f self-host/compose.yaml stop`，再备份整个数据卷。

## 在工作机配置发布目标

本机分享工具只用 Python 3 标准库。在工作机检出本 fork，进入仓库根目录。通过密码管理器或 SSH 取得服务器上生成的令牌，在 Bash 中运行以下命令并粘贴令牌。把域名替换成你的自部署地址。

```bash
read -rsp 'Publishing token: ' RYUGI_SELF_HOST_TOKEN && echo
export RYUGI_SELF_HOST_TOKEN
export RYUGI_SELF_HOST_URL=https://artifacts.example.com
```

## 在本地 Hub 里点分享

1. 在设置好环境变量的终端启动分享工具。将路径换成产物所在的项目根目录，有多个项目时可重复传入 `--project-dir`。

   ```bash
   python3 self-host/hub.py --project-dir /path/to/project
   ```

2. 在工作机浏览器打开终端打印的完整授权链接。每次启动后，在要使用的浏览器里打开一次即可。分享工具只监听 `127.0.0.1:8788`，运行期间保持终端开启。
3. 要在原来的 Hub 页面显示按钮，在 Chrome 的 `chrome://extensions` 或 Edge 的 `edge://extensions` 启用开发者模式，选择「加载已解压的扩展」，选中本仓库的 `self-host/browser-extension/`。刷新已打开的 Hub 标签页。
4. 打开一个本地产物，点击右下角的 **分享到自部署**。核对项目、产物和目标域名，点 **生成分享链接**，再点 **复制链接** 发给读者。Hub 首页的同一个按钮会打开产物选择器。

页面按钮支持通过 `localhost` 或 `127.0.0.1` 打开的默认 Hub 端口 27319–27321。使用自定义端口或 IPv6 回环地址时，点击浏览器工具栏里的扩展图标。其他浏览器可直接打开第 2 步的本机页面，手动选择产物并分享。如果地址对应多个项目，页面会要求你选择。

**发给读者的是生成的分享链接。** 终端打印的授权链接用于本机发布，有效期为本次运行。自部署服务器的发布令牌只由本机 Python 进程使用。读者打开快照时，你的工作机可以离线。

## 从命令行推送

```bash
python3 self-host/push.py --project-dir /path/to/project --artifact html/demo
```

这个例子读取 `/path/to/project/.ryugi/artifacts/html/demo/`。要分享设计文档，把定位符换成 `document/spec`。工具会在上传成功后打印完整的分享链接。可以用 `--server URL` 为单次推送指定目标。HTTP 仅用于回环地址上的本机服务或 SSH 隧道。

网页和命令行使用同一套推送逻辑。重新推送同一个项目和定位符会更新原链接，包括移除本地已经删除的文件。服务端拒绝上传或写入失败时，读者仍能打开先前的完整版本。如果上传后连接中断，可以重试。默认用项目绝对路径的哈希区分项目。团队或 CI 需要从多个检出目录更新同一链接时，从第一次发布起就给 `hub.py` 或 `push.py` 传相同的 `--namespace my-team/my-project`。此选项适用于单个项目。

## 读者能看到什么

- HTML 和 slides 直接打开产物自己的 `index.html`，相对路径的样式、脚本和图片随包提供。页面脚本在浏览器沙箱内运行。
- 画板会嵌入 `canvas.json` 引用的本地 HTML，点击标题可以单独打开。文档和 verify 报告可阅读基本的标题、段落、列表、表格和代码。其他类型列出源文件，读者可以打开素材或数据文件。
- 官方 viewer 的演示引擎、在线编辑、选稿和评论回传属于另一套应用。这里分享的是推送时的文件，查看时不会执行服务器端的数据脚本。

每次只发送选中目录中的普通文件。隐藏文件和目录、`node_modules`、`comments.json`、`comment-cursors.json`、`server-info` 和 `*.loro` 会跳过，遇到符号链接会中止。每份快照最多 1,024 个文件，原始总大小和 ZIP 大小各限 64 MiB。跨产物引用需要单独分享。

这条推送命令直接连接指定的服务器。官方 `ryugi publish` 和自动发布仍遵循各自的 Cloud 配置。如果这个项目已经开启 Cloud 自动发布，希望只手动推送到自己的服务器，可以在项目根目录运行 `ryugi config --auto-publish off --project`。

## 授权范围

本接收端和推送工具独立实现，部署镜像使用 Python 和 Waitress 运行服务，并随附完整的仓库许可证。分发本仓库受许可覆盖的代码时须保留版权声明、许可证和 Exhibit B。附表 A 的主体仅获得内部工作所需的使用及修改权，商标权须另获授权。[仓库许可证](../LICENSE)

当前专有的 `ryugi-artifacts` 发布包有单独条款。复制、修改或再分发该应用须另获权利人授权。请只推送你有权分享的文件。[npm 1.111.4 发布包，内含 LICENSE](https://registry.npmjs.org/ryugi-artifacts/-/ryugi-artifacts-1.111.4.tgz)
