#include "BeratLaneGraph.h"

void UBeratLaneGraph::Sample(int32 L, float D, FVector& OutPos, FVector& OutDir) const
{
	const FBeratLane& Lane = Lanes[L];
	const TArray<FVector>& P = Lane.Points;
	const TArray<float>& S = Lane.Distance;
	if (P.Num() < 2)
	{
		OutPos = P.Num() ? P[0] : FVector::ZeroVector;
		OutDir = FVector::ForwardVector;
		return;
	}
	D = FMath::Clamp(D, 0.f, S.Last());
	// binary search for the segment holding D
	int32 Lo = 0, Hi = S.Num() - 1;
	while (Hi - Lo > 1)
	{
		const int32 Mid = (Lo + Hi) / 2;
		(S[Mid] <= D ? Lo : Hi) = Mid;
	}
	const float Seg = FMath::Max(S[Hi] - S[Lo], 1e-3f);
	const float F = (D - S[Lo]) / Seg;
	OutPos = FMath::Lerp(P[Lo], P[Hi], F);
	OutDir = (P[Hi] - P[Lo]).GetSafeNormal();
}

void UBeratLaneGraph::Finalize()
{
	Cells.Reset();
	for (int32 L = 0; L < Lanes.Num(); ++L)
	{
		FBeratLane& Lane = Lanes[L];
		Lane.Distance.SetNum(Lane.Points.Num());
		float Acc = 0.f;
		for (int32 i = 0; i < Lane.Points.Num(); ++i)
		{
			if (i > 0)
			{
				Acc += FVector::Dist(Lane.Points[i - 1], Lane.Points[i]);
			}
			Lane.Distance[i] = Acc;
		}
		if (Lane.Points.Num())
		{
			Cells.FindOrAdd(CellOf(Lane.Points[0])).Lanes.Add(L);
		}
	}
}
