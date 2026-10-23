# Plugin Template

复制此目录开始开发你的 WeCi 插件。

## 使用步骤

```bash
# 1. 复制模板
cp -r examples/plugins/template my-plugin
cd my-plugin

# 2. 修改 manifest.json
#    - id: 全局唯一，小写字母开头，2-64 字符
#    - name / version / author / description
#    - permissions: 声明需要的权限

# 3. 实现 plugin.py 中的工具

# 4. 安装前校验
weci plugin validate ./my-plugin

# 5. 安装
weci plugin install ./my-plugin

# 6. 查看详情
weci plugin info my-plugin
```

## 需要修改的地方

1. **manifest.json** — id, name, version, author, description, permissions
2. **plugin.py** — 实现 `MyTool.execute()` 中的逻辑

## 权限

如果工具需要网络、文件系统、进程等能力，在 manifest.json 中声明：

```json
"permissions": ["network", "filesystem.read"]
```

然后通过 `context.http.request(...)`、`context.files.read(...)` 等调用。
未声明权限的调用会抛 `PermissionDenied`。
