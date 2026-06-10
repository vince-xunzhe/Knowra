# Knowra Mobile

iOS + Android 移动端，只读消费云端知识库。React Native + Expo SDK 51 + TypeScript。

## 跑起来

### 一次性安装

```bash
cd mobile
npm install
```

### 在 iPhone 上预览（Expo Go，最快）

1. iPhone 装 [Expo Go](https://apps.apple.com/us/app/expo-go/id982107779)（免费，免开发者账号）
2. 启动 dev server：
   ```bash
   npm start
   ```
3. 用 iPhone 摄像头扫码（终端里的 QR），系统会跳转到 Expo Go 加载

要求 iPhone 和 Mac 在**同一个 Wi-Fi**。

### iOS Simulator

需要装 Xcode 后：
```bash
npm run ios
```

## 配置

第一次打开，进 **设置** tab 填三项 + OpenAI key：

| 字段 | 来源 |
|---|---|
| Supabase URL | Supabase 项目 → Settings → API → Project URL |
| Supabase anon key | Supabase 项目 → Settings → API → anon public（**不是** service_role） |
| 云后端 URL | Fly.io 部署后的 URL，如 `https://knowra-cloud.fly.dev` |
| OpenAI API key | platform.openai.com → API keys（只在 Ask 调用时本机送一次，云端不存） |

填完点保存，回 **登录** tab 注册或登录。

## 数据流

```
桌面端 ─push─▶ 云后端 (Supabase Postgres + Storage)
                  │
                  └─pull─▶ 移动端 (snapshot + Ask)
```

- 桌面端跑「立即同步」往云端推（见桌面端 设置 → 云同步）
- 移动端启动后 `cloudSnapshot()` 拉所有论文 / 概念 / wiki 元数据
- 点单个 wiki 才会去 Storage 拉那一篇的 markdown bytes（签名 URL 90s 有效）
- Ask 由云端 agent 跑：检索本地概念上下文 → 调你的 OpenAI key → 返结果 + citations

## 目录

```
mobile/
├── App.tsx                            # 根：Provider + Navigator
├── index.ts                           # Expo 入口
├── app.json                           # Expo config (bundleId, name, …)
├── src/
│   ├── api/cloud.ts                   # Supabase Auth + Knowra cloud REST
│   ├── contexts/
│   │   ├── AuthContext.tsx            # 全局 session
│   │   └── SnapshotContext.tsx        # 全局快照缓存
│   ├── navigation/
│   │   ├── AppNavigator.tsx           # Auth gate + 4 tabs
│   │   └── types.ts                   # 路由参数类型
│   └── screens/
│       ├── LoginScreen.tsx            # 登录 / 注册
│       ├── PapersScreen.tsx           # 论文列表
│       ├── ConceptsScreen.tsx         # 概念列表
│       ├── WikiDetailScreen.tsx       # 单页 markdown（原文）
│       ├── AskScreen.tsx              # 跨论文提问
│       └── SettingsScreen.tsx         # 云端配置 + OpenAI key + 登出
```

## v1 范围（也即"不做"清单）

- ✅ 登录 / 注册 / 登出
- ✅ 论文列表 + 概念列表 + 搜索 + 下拉刷新
- ✅ Wiki 文档查看（原始 markdown 文本）
- ✅ Ask 跨论文提问（带 citations）
- ❌ Markdown 富文本渲染（v2，等需求确认再加 `react-native-markdown-display`）
- ❌ 图谱可视化（mobile 屏幕太小，看板 / 关系图留桌面）
- ❌ 离线缓存（snapshot 全部在内存；下拉刷新拉新）
- ❌ Push 通知 / EAS Build / TestFlight（v3 准备上架时再做）

## 调试

如果连不上云后端：
- 设置里点保存后**完全杀掉 app 重开**一次，确认 AsyncStorage 写入
- 用 `curl https://knowra-cloud.fly.dev/api/cloud/me -H "Authorization: Bearer <token>"` 验证云端是否活
- Metro bundler 日志在终端，看 axios 报错码（401 = token 过期、403 = RLS、404 = URL 拼错）
