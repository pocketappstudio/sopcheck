import { NextResponse, type NextRequest } from "next/server";

// UX-level route protection. The API enforces auth/roles/tenancy on every request regardless.
const COOKIE = "opschk_session";

function decode(token: string): { role?: string; exp?: number } | null {
  try {
    const part = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    return JSON.parse(atob(part + "=".repeat((4 - (part.length % 4)) % 4)));
  } catch {
    return null;
  }
}

export function middleware(req: NextRequest) {
  const { pathname, search } = req.nextUrl;
  const token = req.cookies.get(COOKIE)?.value;
  const claims = token ? decode(token) : null;
  const valid = !!claims && (!claims.exp || claims.exp * 1000 > Date.now());
  const home = claims?.role === "technician" ? "/technician" : "/manager";

  if (pathname === "/login") {
    // a revoked/deleted session still carries a decodable cookie; let the login page render in that case
    const stale = req.nextUrl.searchParams.has("expired") || req.nextUrl.searchParams.has("deleted");
    return valid && !stale ? NextResponse.redirect(new URL(home, req.url)) : NextResponse.next();
  }
  if (!valid) {
    const url = new URL("/login", req.url);
    url.searchParams.set("next", pathname + search);
    return NextResponse.redirect(url);
  }
  if (pathname.startsWith("/manager") && claims?.role === "technician") {
    return NextResponse.redirect(new URL("/technician", req.url));
  }
  return NextResponse.next();
}

export const config = { matcher: ["/manager/:path*", "/technician/:path*", "/login"] };
