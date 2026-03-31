import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

export function middleware(request: NextRequest) {
  // Check for session cookie
  const sessionToken = request.cookies.get("session");

  // Protect /workspace routes
  if (request.nextUrl.pathname.startsWith("/workspace")) {
    if (!sessionToken) {
      const loginUrl = new URL("/login", request.url);
      return NextResponse.redirect(loginUrl);
    }
  }

  // Redirect logged-in users away from /login
  if (request.nextUrl.pathname === "/login" && sessionToken) {
    const workspaceUrl = new URL("/workspace/chats/new", request.url);
    return NextResponse.redirect(workspaceUrl);
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/workspace/:path*", "/login"],
};