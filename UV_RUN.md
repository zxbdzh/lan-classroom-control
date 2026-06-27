# uv 运行说明

本项目推荐使用 `uv` 管理 Python 版本、虚拟环境和依赖。项目已在 `pyproject.toml` 中声明依赖与入口脚本。

## 初始化环境

```powershell
uv sync --python 3.8
```

项目限制 `requires-python = ">=3.8,<3.9"`，固定使用 Python 3.8 以兼容 Windows 7。

## 启动教师端

```powershell
uv run teacher
```

也可以直接运行入口文件：

```powershell
uv run python teacher\main.py
```

## 启动学生端

```powershell
uv run student
```

学生端的键鼠禁用、网络管控等完整功能需要管理员权限。

## 运行测试

```powershell
uv run pytest tests -v
```

## 构建依赖

发布打包需要额外同步 build 依赖：

```powershell
uv sync --group build
```
