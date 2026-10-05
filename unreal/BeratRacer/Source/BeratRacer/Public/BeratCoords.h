#pragma once

#include "CoreMinimal.h"

// Map package axes: metres, x east, y north, z up (NGF). Unreal: centimetres, X east, Y south, Z up.
namespace BeratCoords
{
	inline FVector FromPackage(double X, double Y, double Z) { return FVector(X * 100.0, -Y * 100.0, Z * 100.0); }
	inline FVector ToPackage(const FVector& V) { return FVector(V.X / 100.0, -V.Y / 100.0, V.Z / 100.0); }
	// Package heading: degrees clockwise from north. Unreal yaw: degrees from +X (east), clockwise seen from above (Y south).
	inline double YawFromHeading(double HeadingDeg) { return HeadingDeg - 90.0; }
}
