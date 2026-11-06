# NodesRadar 订阅镜像

GitHub Actions 每 12 小时读取 NodesRadar 首页和来源目录。Clash 推荐订阅发布到 `feeds/clash.yaml`，各来源按原格式发布到 `feeds/by-source/`。`feeds/index.json` 提供固定文件路径、节点数和 SHA-256，供客户端同步。每个来源单独验证，失败时保留上次可用文件。

上游地址从 [NodesRadar 首页](https://nodesradar.com/)和[来源目录](https://nodesradar.com/sources/)读取。公开订阅的内容会随上游变化。
