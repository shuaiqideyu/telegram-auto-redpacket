# 前端（Vue 3 + Element Plus）

Web 控制台，前后端分离。开发时由 Vite 代理 `/api` 到后端 `:8000`。

## 开发

```bash
npm install
npm run dev      # http://localhost:5173
```

确保后端已启动（项目根目录 `python3 main.py`）。

## 构建

```bash
npm run build    # 产物 dist/，由后端 FastAPI 挂载
```
