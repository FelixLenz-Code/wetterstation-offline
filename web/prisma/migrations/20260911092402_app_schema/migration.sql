-- CreateSchema
CREATE SCHEMA IF NOT EXISTS "app";

-- CreateEnum
CREATE TYPE "app"."AlertKind" AS ENUM ('FROST', 'STORM', 'THUNDERSTORM', 'HEAVY_RAIN');

-- CreateTable
CREATE TABLE "app"."app_setting" (
    "key" TEXT NOT NULL,
    "value" JSONB NOT NULL,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "app_setting_pkey" PRIMARY KEY ("key")
);

-- CreateTable
CREATE TABLE "app"."push_subscription" (
    "id" TEXT NOT NULL,
    "endpoint" TEXT NOT NULL,
    "p256dh" TEXT NOT NULL,
    "auth" TEXT NOT NULL,
    "userAgent" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "lastSeen" TIMESTAMP(3),
    "failures" INTEGER NOT NULL DEFAULT 0,

    CONSTRAINT "push_subscription_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "app"."alert_rule" (
    "id" TEXT NOT NULL,
    "kind" "app"."AlertKind" NOT NULL,
    "enabled" BOOLEAN NOT NULL DEFAULT true,
    "threshold" DOUBLE PRECISION NOT NULL,
    "minProbability" DOUBLE PRECISION NOT NULL DEFAULT 0.5,
    "cooldownMinutes" INTEGER NOT NULL DEFAULT 360,
    "lastFiredAt" TIMESTAMP(3),

    CONSTRAINT "alert_rule_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "push_subscription_endpoint_key" ON "app"."push_subscription"("endpoint");

-- CreateIndex
CREATE UNIQUE INDEX "alert_rule_kind_key" ON "app"."alert_rule"("kind");
