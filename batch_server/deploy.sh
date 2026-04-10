#!/bin/bash
# 사용법:
#   ./deploy.sh 1.0.0 dev    # dev 배포
#   ./deploy.sh 1.0.0 prod   # prod 배포
#   ./deploy.sh 1.0.0        # 빌드만

set -e

VERSION=${1:?"버전을 입력하세요. 예: ./deploy.sh 1.0.0"}
TARGET=${2:-""}
IMAGE="batch-automation:$VERSION"

build() {
    echo "▶ 이미지 빌드: $IMAGE"
    docker build -t "$IMAGE" .
    echo "✔ 빌드 완료: $IMAGE"
}

deploy_dev() {
    echo "▶ dev 배포: $IMAGE → :15000"
    VERSION=$VERSION docker compose -f docker-compose.dev.yml up -d --no-build
    echo "✔ dev 실행 중 → http://localhost:15000/docs"
}

deploy_prod() {
    echo "▶ prod 배포: $IMAGE → :5000"
    VERSION=$VERSION docker compose -f docker-compose.prod.yml up -d --no-build
    echo "✔ prod 실행 중 → http://localhost:5000/docs"
}

# 항상 빌드 먼저
build

case "$TARGET" in
    dev)  deploy_dev  ;;
    prod) deploy_prod ;;
    "")
        echo ""
        echo "빌드만 완료됐습니다. 배포하려면:"
        echo "  dev  → ./deploy.sh $VERSION dev"
        echo "  prod → ./deploy.sh $VERSION prod"
        ;;
    *)
        echo "알 수 없는 타겟: $TARGET (dev 또는 prod)"
        exit 1
        ;;
esac
