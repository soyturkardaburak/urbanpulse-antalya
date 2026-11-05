export function distanceMetres(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const earth = 6371000;
  const phi1 = (lat1 * Math.PI) / 180;
  const phi2 = (lat2 * Math.PI) / 180;
  const dPhi = ((lat2 - lat1) * Math.PI) / 180;
  const dLambda = ((lon2 - lon1) * Math.PI) / 180;
  const a = Math.sin(dPhi / 2) ** 2 + Math.cos(phi1) * Math.cos(phi2) * Math.sin(dLambda / 2) ** 2;
  return 2 * earth * Math.asin(Math.sqrt(a));
}

export function walkMinutes(metres: number): number {
  if (metres <= 40) {
    return 0;
  }
  return Math.max(1, Math.round(metres / 80));
}
