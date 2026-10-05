#pragma once

#include "CoreMinimal.h"
#include "GameFramework/GameModeBase.h"
#include "GameFramework/HUD.h"
#include "GameFramework/PlayerController.h"
#include "BeratGameMode.generated.h"

class ABeratCar;
class ABeratTimeOfDay;

// Spawns the player's car at the map's spawn (the PlayerStart the importer places) and swaps cars on request.
UCLASS()
class BERATRACER_API ABeratGameMode : public AGameModeBase
{
	GENERATED_BODY()

public:
	ABeratGameMode();

	// The garage: sport, everyday, off-road, pickup (vehicle blueprints derived from ABeratCar).
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Berat") TArray<TSubclassOf<ABeratCar>> Cars;

	virtual APawn* SpawnDefaultPawnFor_Implementation(AController* NewPlayer, AActor* StartSpot) override;

	// Replace the player's car by the next one in the garage, at the same place and speed.
	void NextCar(APlayerController* PC);

private:
	int32 Current = 0;
};

UCLASS()
class BERATRACER_API ABeratPlayerController : public APlayerController
{
	GENERATED_BODY()

public:
	ABeratPlayerController();

protected:
	virtual void SetupInputComponent() override;

private:
	void NextCar();
	void TimeForward();
	void TimeBack();
	ABeratTimeOfDay* Clock() const;
};

// Speed, gear, clock and the controls, drawn with the canvas (no UMG asset needed).
UCLASS()
class BERATRACER_API ABeratHUD : public AHUD
{
	GENERATED_BODY()

public:
	virtual void DrawHUD() override;

private:
	float FpsSmooth = 60.f;
};
