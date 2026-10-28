#!/usr/bin/env bash
# Azure App Service host for the C27 hosted demo (branch razeef/azure-deploy).
# Run from the repository root in Azure Cloud Shell (Bash), signed in to the
# subscription that should pay:
#
#   bash deploy/azure/deploy.sh          # first deploy: creates everything
#   bash deploy/azure/deploy.sh update   # later: rebuild the image and restart
#
# It builds the same Dockerfile in Azure Container Registry (no local Docker),
# then runs it on one Linux B1 App Service with Always On. Demo mode only: no
# PayPal sandbox or model keys are set, and the container refuses to boot if any
# are. The registry is pulled with the app's managed identity, so no registry
# password is stored. Override names with environment variables, e.g.
#   LOCATION=centralindia bash deploy/azure/deploy.sh
set -Eeuo pipefail

SUB_ID="$(az account show --query id -o tsv)"
SUFFIX="${SUFFIX:-${SUB_ID:0:6}}"          # stable per subscription; names below are global
RG="${RG:-countersign-demo-rg}"
LOCATION="${LOCATION:-eastus}"
PLAN="${PLAN:-countersign-demo-plan}"
SKU="${SKU:-B1}"
APP="${APP:-countersign-demo-$SUFFIX}"
ACR="${ACR:-countersigndemo$SUFFIX}"
TAG="$(git rev-parse --short HEAD)"
IMAGE="$ACR.azurecr.io/countersign:$TAG"

log() { printf '[azure] %s\n' "$*"; }

build() {
  log "building $IMAGE in the registry (about 5 minutes)"
  az acr build --registry "$ACR" --image "countersign:$TAG" --file Dockerfile . --output none
}

if [ "${1:-}" = update ]; then
  build
  az webapp config container set -g "$RG" -n "$APP" --container-image-name "$IMAGE" --output none
  az webapp restart -g "$RG" -n "$APP"
  log "updated to $TAG: https://$APP.azurewebsites.net"
  exit 0
fi

log "registering resource providers"
az provider register --namespace Microsoft.Web --wait
az provider register --namespace Microsoft.ContainerRegistry --wait

log "resource group $RG in $LOCATION"
az group create -n "$RG" -l "$LOCATION" --output none
az acr create -g "$RG" -n "$ACR" --sku Basic --admin-enabled false --output none
build

log "App Service plan $PLAN ($SKU, Linux)"
az appservice plan create -g "$RG" -n "$PLAN" --is-linux --sku "$SKU" --output none
az webapp create -g "$RG" -p "$PLAN" -n "$APP" --container-image-name "$IMAGE" --output none

log "pulling the registry with the app's managed identity"
PRINCIPAL="$(az webapp identity assign -g "$RG" -n "$APP" --query principalId -o tsv)"
az role assignment create --assignee-object-id "$PRINCIPAL" --assignee-principal-type ServicePrincipal \
  --role AcrPull --scope "$(az acr show -n "$ACR" --query id -o tsv)" --output none
az webapp config set -g "$RG" -n "$APP" --always-on true \
  --generic-configurations '{"acrUseManagedIdentityCreds": true, "healthCheckPath": "/api/v1/meta"}' \
  --output none

# WEBSITES_ENABLE_APP_SERVICE_STORAGE=false: a /home mount would hide the
# migrated database template the image keeps in /home/app.
az webapp config appsettings set -g "$RG" -n "$APP" --output none --settings \
  WEBSITES_PORT=8080 \
  WEBSITES_ENABLE_APP_SERVICE_STORAGE=false \
  WEBSITES_CONTAINER_START_TIME_LIMIT=600 \
  COUNTERSIGN_HOSTED_SEED=none
az webapp log config -g "$RG" -n "$APP" --docker-container-logging filesystem --output none
az webapp config container set -g "$RG" -n "$APP" --container-image-name "$IMAGE" --output none
az webapp restart -g "$RG" -n "$APP"

log "deployed $TAG: https://$APP.azurewebsites.net (first boot takes a minute)"
log "logs: az webapp log tail -g $RG -n $APP"
