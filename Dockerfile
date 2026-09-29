# Решатели (C++), пакет dispatch и кэши координат, матриц и линий маршрутов для демо-данных.
FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends clang make && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY solver solver
RUN make -C solver CXX=clang++
COPY requirements.txt ./
RUN pip install --no-cache-dir openpyxl==3.1.5
COPY dispatch dispatch
COPY cache cache
COPY data/demo data/demo
COPY scripts scripts
ENV DISPATCH_CACHE=/app/cache DISPATCH_SOLVER_BIN=/app/solver/bin
EXPOSE 8080
CMD ["scripts/demo.sh"]
