import InspectionForm from "../components/inspectionForm";

export default function InspectionPage() {
  const mockInspectionId = "105";

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="bg-white border-b border-slate-200 px-6 py-4 flex items-center justify-between">
        <span className="text-xl font-bold tracking-tight text-slate-900">
          Ckeckii
        </span>
        <span className="text-xs font-medium text-slate-400 bg-slate-100 px-2 py-0.5 rounded-full">
          Operario
        </span>
      </header>

      <main className="max-w-lg mx-auto px-4 py-10">
        <div className="mb-6">
          <h1 className="text-xl font-semibold text-slate-900">
            Inspección #{mockInspectionId}
          </h1>
          <p className="mt-1 text-sm text-slate-500">
            Registra los daños encontrados durante el check-out
          </p>
        </div>
        <InspectionForm inspectionId={mockInspectionId} />
      </main>
    </div>
  );
}
