# kira-ai-plugin-mijia

[KiraAI](https://github.com/xxynet/KiraAI) 米家智能家居插件，基于 [mijiaAPI](https://github.com/Do1e/mijia-api) 实现。

通过自然语言控制小米智能家居设备，无需额外配置 MCP 服务器。

## 功能

- 应用内扫码登录，无需终端操作；后台长轮询自动保存凭证
- 查看家庭和设备列表
- 获取设备状态和属性
- 控制设备（设置属性、执行操作）
- 查看和执行智能场景
- 查询耗材状态
- 会话级权限控制

## 安装

1. 将本文件夹复制到 `data/plugins/kira-ai-plugin-mijia/`
2. 安装依赖：
   ```
   pip install mijiaAPI>=3.2.0
   ```
3. 重启 KiraAI

## 配置

| 字段 | 类型 | 说明 |
|------|------|------|
| `enabled_sessions` | 列表 | 限制可使用米家工具的会话，格式为 `<adapter>:<gm/dm>:<session_id>`，留空则所有会话可用 |
| `auth_path` | 字符串 | 自定义认证文件路径，留空则使用默认路径 `data/plugin_data/kira-ai-plugin-mijia/auth.json` |
| `default_home_id` | 字符串 | 默认家庭 ID，留空则查询所有家庭 |

## 使用

1. 在聊天中让AI登录米家，会显示二维码。二维码显示后，插件会在后台保持登录长轮询。
2. 使用米家 App 扫描二维码，插件会在登录成功后自动保存认证凭证。
3. 扫码后，向AI确认已完成扫码；AI会调用 `mijia_login_check` 验证已保存凭证。
4. 凭证验证成功后，通过自然语言控制设备：
   - "列出我的米家设备"
   - "打开客厅台灯"
   - "把空调温度调到26度"
   - "执行晚安场景"

## 工具列表

| 工具名 | 说明 |
|--------|------|
| `mijia_login` | 发起二维码登录，并在后台长轮询等待扫码结果 |
| `mijia_login_check` | 仅在用户确认已扫码后调用，用于验证已保存凭证是否有效 |
| `mijia_list_homes` | 查看所有家庭 |
| `mijia_list_devices` | 查看设备列表 |
| `mijia_device_status` | 获取设备状态和可用操作 |
| `mijia_control_device` | 控制设备（设置属性/执行操作） |
| `mijia_list_scenes` | 查看智能场景 |
| `mijia_run_scene` | 执行场景 |
| `mijia_list_consumables` | 查询耗材状态 |

## 许可证

[AGPL-3.0](LICENSE)
