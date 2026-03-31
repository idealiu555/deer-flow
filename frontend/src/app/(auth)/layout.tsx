export default function AuthLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // Middleware handles all auth redirects
  return <>{children}</>;
}