VERSION   ?= 0.11.6
BASE_IMAGE ?= ubuntu:24.04
PORT      ?= 8039
CONTAINER ?= orcaweb3d
APPIMAGE  := vendor/OrcaSlicer.AppImage
ORCA_VER  := 2.4.2
ORCA_URL  := https://github.com/OrcaSlicer/OrcaSlicer/releases/download/v$(ORCA_VER)/OrcaSlicer_Linux_AppImage_Ubuntu2404_aarch64_V$(ORCA_VER).AppImage
# 国内拉不动 github release 时的加速前缀
MIRROR    ?= https://ghfast.top/

.PHONY: appimage build up down stop logs shell test clean web app

# 热更前端：NAS 上的文件权限是 700 + uid 984，容器里跑的是 uid 1000，
# 拷进去不 chmod 会导致静态文件读不到（浏览器报 ERR_CONTENT_LENGTH_MISMATCH、整页白屏）。
web:
	docker cp web/app.js        $(CONTAINER):/app/web/app.js
	docker cp web/form.js       $(CONTAINER):/app/web/form.js
	docker cp web/plate3d.js    $(CONTAINER):/app/web/plate3d.js
	docker cp web/plate.js      $(CONTAINER):/app/web/plate.js
	docker cp web/style.css     $(CONTAINER):/app/web/style.css
	docker cp web/print.css     $(CONTAINER):/app/web/print.css
	docker cp web/index.html    $(CONTAINER):/app/web/index.html
	docker cp viewer/index.html $(CONTAINER):/app/viewer/index.html
	docker exec -u 0 $(CONTAINER) chmod -R a+rX /app/web /app/viewer
	@echo "前端已热更；浏览器请强刷（Ctrl/Cmd+Shift+R）"

# 热更后端（改 py 必须重启，uvicorn 没开 reload）
app:
	docker cp app/. $(CONTAINER):/app/app/
	docker exec -u 0 $(CONTAINER) chmod -R a+rX /app/app
	docker restart $(CONTAINER)
	@echo "后端已热更并重启"

appimage:
	@mkdir -p vendor
	@test -s $(APPIMAGE) || (echo "downloading OrcaSlicer $(ORCA_VER) ..."; \
	  curl -fSL --http1.1 --retry 3 -o $(APPIMAGE) "$(MIRROR)$(ORCA_URL)" && chmod +x $(APPIMAGE))
	@ls -lh $(APPIMAGE)

build: appimage
	docker build --build-arg BASE_IMAGE=$(BASE_IMAGE) -t orca-slicer-node:$(VERSION) .

up:
	docker compose up -d --build

down:
	docker compose down

stop:
	docker compose stop

logs:
	docker compose logs -f --tail 50

shell:
	docker compose exec slicer-node /bin/bash

test:
	@curl -sS http://127.0.0.1:$(PORT)/health | python3 -m json.tool

# 只在明确要求时清（数据目录里有 gcode，先看一眼再删）
clean:
	docker image prune -f
