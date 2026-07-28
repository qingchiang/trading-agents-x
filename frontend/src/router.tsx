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

export type RouterLocation = {
  pathname: string;
  search: string;
  hash: string;
};

type RouterValue = {
  location: RouterLocation;
  navigate: (to: string, options?: NavigateOptions) => void;
};

const RouterContext = createContext<RouterValue | null>(null);

export function Router({
  children,
  initialPath,
}: PropsWithChildren<{ initialPath?: string }>) {
  const browserBacked = initialPath === undefined;
  const [location, setLocation] = useState<RouterLocation>(() =>
    parseLocation(initialPath ?? browserLocation()),
  );

  useEffect(() => {
    if (!browserBacked) return;
    const syncLocation = () => setLocation(parseLocation(browserLocation()));
    window.addEventListener("popstate", syncLocation);
    return () => window.removeEventListener("popstate", syncLocation);
  }, [browserBacked]);

  const navigate = useCallback(
    (to: string, options?: NavigateOptions) => {
      const next = parseLocation(to);
      if (browserBacked) {
        const method = options?.replace ? "replaceState" : "pushState";
        window.history[method](null, "", locationPath(next));
      }
      setLocation(next);
    },
    [browserBacked],
  );

  const value = useMemo(() => ({ location, navigate }), [location, navigate]);
  return (
    <RouterContext.Provider value={value}>{children}</RouterContext.Provider>
  );
}

export function useNavigate() {
  return useRouter().navigate;
}

export function usePathname() {
  return useRouter().location.pathname;
}

export function useLocation() {
  return useRouter().location;
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
  const target = parseLocation(to).pathname;
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

function normalizePathname(pathname: string) {
  if (!pathname || pathname === "/") return "/";
  return `/${pathname.replace(/^\/+|\/+$/g, "")}`;
}

function parseLocation(path: string): RouterLocation {
  const url = new URL(path || "/", "http://tradingagents.local");
  return {
    pathname: normalizePathname(url.pathname),
    search: url.search,
    hash: url.hash,
  };
}

function locationPath(location: RouterLocation) {
  return `${location.pathname}${location.search}${location.hash}`;
}

function browserLocation() {
  return `${window.location.pathname}${window.location.search}${window.location.hash}`;
}
