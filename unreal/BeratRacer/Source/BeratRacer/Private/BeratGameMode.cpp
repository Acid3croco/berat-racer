#include "BeratGameMode.h"

#include "BeratCar.h"
#include "BeratTimeOfDay.h"
#include "ChaosVehicleMovementComponent.h"
#include "Components/SkeletalMeshComponent.h"
#include "Engine/Canvas.h"
#include "Engine/Engine.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "GameFramework/PlayerStart.h"

ABeratGameMode::ABeratGameMode()
{
	PlayerControllerClass = ABeratPlayerController::StaticClass();
	HUDClass = ABeratHUD::StaticClass();
	DefaultPawnClass = nullptr;
}

APawn* ABeratGameMode::SpawnDefaultPawnFor_Implementation(AController* NewPlayer, AActor* StartSpot)
{
	if (Cars.Num() == 0 || !Cars[Current])
	{
		return Super::SpawnDefaultPawnFor_Implementation(NewPlayer, StartSpot);
	}
	FActorSpawnParameters P;
	P.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AdjustIfPossibleButAlwaysSpawn;
	P.Instigator = GetInstigator();
	const FTransform T = StartSpot ? StartSpot->GetActorTransform() : FTransform::Identity;
	return GetWorld()->SpawnActor<ABeratCar>(Cars[Current], T.GetLocation() + FVector(0, 0, 60.f), T.Rotator(), P);
}

void ABeratGameMode::NextCar(APlayerController* PC)
{
	if (Cars.Num() < 2 || !PC)
	{
		return;
	}
	APawn* Old = PC->GetPawn();
	FVector At = Old ? Old->GetActorLocation() : FVector::ZeroVector;
	FRotator Rot = Old ? FRotator(0, Old->GetActorRotation().Yaw, 0) : FRotator::ZeroRotator;
	const FVector Vel = Old ? Old->GetVelocity() : FVector::ZeroVector;
	Current = (Current + 1) % Cars.Num();
	if (Old)
	{
		PC->UnPossess();
		Old->Destroy();
	}
	FActorSpawnParameters P;
	P.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AdjustIfPossibleButAlwaysSpawn;
	ABeratCar* Car = GetWorld()->SpawnActor<ABeratCar>(Cars[Current], At + FVector(0, 0, 80.f), Rot, P);
	if (Car)
	{
		PC->Possess(Car);
		Car->GetMesh()->SetPhysicsLinearVelocity(Vel);
	}
}

ABeratPlayerController::ABeratPlayerController()
{
	bAutoManageActiveCameraTarget = true;
}

void ABeratPlayerController::SetupInputComponent()
{
	Super::SetupInputComponent();
	// Plain key bindings for the few menu-like actions; driving input lives on the car (Enhanced Input).
	InputComponent->BindKey(EKeys::Tab, IE_Pressed, this, &ABeratPlayerController::NextCar);
	InputComponent->BindKey(EKeys::Gamepad_DPad_Right, IE_Pressed, this, &ABeratPlayerController::NextCar);
	InputComponent->BindKey(EKeys::T, IE_Pressed, this, &ABeratPlayerController::TimeForward);
	InputComponent->BindKey(EKeys::Gamepad_DPad_Left, IE_Pressed, this, &ABeratPlayerController::TimeForward);
	InputComponent->BindKey(EKeys::Y, IE_Pressed, this, &ABeratPlayerController::TimeBack);
	InputComponent->BindKey(EKeys::Gamepad_DPad_Down, IE_Pressed, this, &ABeratPlayerController::TimeBack);
}

void ABeratPlayerController::NextCar()
{
	if (ABeratGameMode* GM = GetWorld()->GetAuthGameMode<ABeratGameMode>())
	{
		GM->NextCar(this);
	}
}

ABeratTimeOfDay* ABeratPlayerController::Clock() const
{
	for (TActorIterator<ABeratTimeOfDay> It(GetWorld()); It; ++It)
	{
		return *It;
	}
	return nullptr;
}

void ABeratPlayerController::TimeForward()
{
	if (ABeratTimeOfDay* C = Clock())
	{
		C->SkipHours(1.f);
	}
}

void ABeratPlayerController::TimeBack()
{
	if (ABeratTimeOfDay* C = Clock())
	{
		C->SkipHours(-1.f);
	}
}

void ABeratHUD::DrawHUD()
{
	Super::DrawHUD();
	if (!Canvas)
	{
		return;
	}
	const float Dt = GetWorld()->GetDeltaSeconds();
	if (Dt > 0.f)
	{
		FpsSmooth = FMath::Lerp(FpsSmooth, 1.f / Dt, 0.05f);
	}
	UFont* Big = GEngine->GetLargeFont();
	UFont* Small = GEngine->GetSmallFont();
	const float X = Canvas->ClipX - 260.f, Y = Canvas->ClipY - 150.f;
	if (const ABeratCar* Car = Cast<ABeratCar>(GetOwningPawn()))
	{
		const int32 Gear = Car->GetGear();
		const FString GearText = Gear < 0 ? TEXT("R") : Gear == 0 ? TEXT("N") : FString::FromInt(Gear);
		DrawText(FString::Printf(TEXT("%3.0f km/h"), FMath::Abs(Car->GetSpeedKmh())), FLinearColor::White, X, Y, Big, 2.2f);
		DrawText(FString::Printf(TEXT("gear %s   %4.0f rpm"), *GearText, Car->GetRpm()), FLinearColor(0.85f, 0.85f, 0.85f), X, Y + 50.f, Small, 1.4f);
		DrawText(Car->DisplayName.ToString(), FLinearColor(1.f, 0.8f, 0.4f), X, Y + 75.f, Small, 1.4f);
	}
	for (TActorIterator<ABeratTimeOfDay> It(GetWorld()); It; ++It)
	{
		const float H = It->Hours;
		DrawText(FString::Printf(TEXT("%02d:%02d"), int32(H), int32(FMath::Fmod(H, 1.f) * 60.f)), FLinearColor::White, X, Y + 100.f, Small, 1.4f);
		break;
	}
	DrawText(FString::Printf(TEXT("%.0f fps"), FpsSmooth), FLinearColor(0.6f, 1.f, 0.6f), 20.f, 20.f, Small, 1.2f);
	DrawText(TEXT("Tab / D-pad right: next car   T / Y: time +-1 h   L: lights   C: camera   R: reset"),
		FLinearColor(1.f, 1.f, 1.f, 0.6f), 20.f, Canvas->ClipY - 30.f, Small, 1.f);
}
