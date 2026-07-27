import {
  AnchorHTMLAttributes,
  MouseEvent,
  PropsWithChildren,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

type NavigateOptions = {
  replace?: boolean;
};

type RouterValue = {
  pathname: string;
  navigate: (to: string, options?: NavigateOptions) => void;
};

const RouterContext = createContext<RouterValue | null>(null);

export function Router({
  children,
  initialPath,
}: PropsWithChildren<{ initialPath?: string }>) {
  const browserBacked = initialPath === undefined;
  const [pathname, setPathname] = useState(
    initialPath ?? window.location.pathname,
  );

  useEffect(() => {
    if (!browserBacked) return;
    const syncLocation = () => setPathname(window.location.pathname);
    window.addEventListener("popstate", syncLocation);
    return () => window.removeEventListener("popstate", syncLocation);
  }, [browserBacked]);

  const navigate = useCallback(
    (to: string, options?: NavigateOptions) => {
      const next = normalizePath(to);
      if (browserBacked) {
        const method = options?.replace ? "replaceState" : "pushState";
        window.history[method](null, "", next);
      }
      setPathname(next);
    },
    [browserBacked],
  );

  const value = useMemo(() => ({ pathname, navigate }), [navigate, pathname]);
  return (
    <RouterContext.Provider value={value}>{children}</RouterContext.Provider>
  );
}

export function useNavigate() {
  return useRouter().navigate;
}

export function usePathname() {
  return useRouter().pathname;
}

export function useParams() {
  const pathname = usePathname();
  const match = /^\/runs\/([^/]+)\/?$/.exec(pathname);
  return { runId: match ? decodeURIComponent(match[1]) : undefined };
}

type LinkProps = Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "href"> & {
  to: string;
};

export function Link({ children, onClick, to, ...props }: LinkProps) {
  const navigate = useNavigate();
  const follow = (event: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(event);
    if (
      event.defaultPrevented ||
      event.button !== 0 ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey
    ) {
      return;
    }
    event.preventDefault();
    navigate(to);
  };
  return (
    <a href={to} onClick={follow} {...props}>
      {children}
    </a>
  );
}

type NavLinkProps = Omit<LinkProps, "className"> & {
  className?: string | ((state: { isActive: boolean }) => string);
  end?: boolean;
};

export function NavLink({
  className,
  end = false,
  to,
  ...props
}: NavLinkProps) {
  const pathname = usePathname();
  const target = normalizePath(to);
  const isActive = end
    ? pathname === target
    : pathname === target || pathname.startsWith(`${target}/`);
  const resolvedClassName =
    typeof className === "function" ? className({ isActive }) : className;
  return <Link className={resolvedClassName} to={to} {...props} />;
}

function useRouter() {
  const context = useContext(RouterContext);
  if (!context) {
    throw new Error("Router components must be rendered inside Router");
  }
  return context;
}

function normalizePath(path: string) {
  const [pathname] = path.split(/[?#]/, 1);
  if (!pathname || pathname === "/") return "/";
  return `/${pathname.replace(/^\/+|\/+$/g, "")}`;
}
