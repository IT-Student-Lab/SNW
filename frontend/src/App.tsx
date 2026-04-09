import { BrowserRouter, Routes, Route } from "react-router-dom";
import { AuthProvider } from "./AuthContext";
import AppLayout from "./AppLayout";
import LoginPage from "./pages/LoginPage";
import GeneratorPage from "./pages/GeneratorPage";
import HistoryPage from "./pages/HistoryPage";
import ResultsPage from "./pages/ResultsPage";
import TemplatePage from "./pages/TemplatePage";

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route element={<AppLayout />}>
            <Route path="/" element={<GeneratorPage />} />
            <Route path="/history" element={<HistoryPage />} />
            <Route path="/sjabloon" element={<TemplatePage />} />
            <Route path="/results/:jobId" element={<ResultsPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
