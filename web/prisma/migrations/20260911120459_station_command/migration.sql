-- CreateEnum
CREATE TYPE "app"."CommandState" AS ENUM ('PENDING', 'SENT', 'FAILED');

-- CreateTable
CREATE TABLE "app"."station_command" (
    "id" TEXT NOT NULL,
    "stationKey" TEXT NOT NULL,
    "sensorKey" TEXT,
    "desiredState" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "state" "app"."CommandState" NOT NULL DEFAULT 'PENDING',
    "sentAt" TIMESTAMP(3),
    "note" TEXT,

    CONSTRAINT "station_command_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "station_command_state_createdAt_idx" ON "app"."station_command"("state", "createdAt");
