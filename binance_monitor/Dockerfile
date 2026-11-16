FROM python:3.11-slim

WORKDIR /app

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制代码
COPY . .

# 创建日志目录
RUN mkdir -p logs

# 非 root 用户运行（可选）
# RUN useradd -m appuser && chown -R appuser:appuser /app
# USER appuser

CMD ["python", "main.py"]
