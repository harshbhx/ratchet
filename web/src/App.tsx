import { lazy, Suspense } from "react";
import { BrowserRouter, Route, Routes } from "react-router";
import { Layout } from "@/components/layout/layout";
import { TourHost } from "@/components/tour/tour";
import { ButtonLink } from "@/components/ui/button";
import { Container } from "@/components/ui/section";

const Landing = lazy(() => import("@/pages/landing"));
const Scout = lazy(() => import("@/pages/scout"));
const Dashboard = lazy(() => import("@/pages/dashboard"));
const Judges = lazy(() => import("@/pages/judges"));
const Live = lazy(() => import("@/pages/live"));

function Loading() {
  return <div className="min-h-[60vh]" aria-busy="true" />;
}

function NotFound() {
  return (
    <Container className="py-32 text-center">
      <p className="font-mono text-[13px] text-muted">404</p>
      <h1 className="mt-3 text-3xl font-semibold tracking-tight">This page does not exist.</h1>
      <ButtonLink to="/" className="mt-8">Back to the story</ButtonLink>
    </Container>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <Suspense fallback={<Loading />}>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Landing />} />
            <Route path="scout" element={<Scout />} />
            <Route path="dashboard" element={<Dashboard />} />
            <Route path="judges" element={<Judges />} />
            <Route path="live" element={<Live />} />
            <Route path="*" element={<NotFound />} />
          </Route>
        </Routes>
      </Suspense>
      <TourHost />
    </BrowserRouter>
  );
}
