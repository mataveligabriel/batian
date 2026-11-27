import React from "react";

// Uma página com erro não derruba o BastiON inteiro (tela branca): mostra o erro e deixa recarregar.
// resetKey (o endereço da página) limpa o erro ao navegar para outra página.
export default class PageErrorBoundary extends React.Component {
  constructor(props) { super(props); this.state = { error: null }; }
  static getDerivedStateFromError(error) { return { error }; }
  componentDidCatch(error, info) { console.error("Erro na página:", error, info?.componentStack); }
  componentDidUpdate(prev) { if (prev.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null }); }
  clearAndReload = () => {
    try { localStorage.removeItem("bastion_avail_clip"); } catch { /* ok */ }
    window.location.reload();
  };
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="flex-1 flex items-center justify-center p-6" role="alert">
        <div className="max-w-lg w-full rounded-lg border border-red-500/40 bg-red-500/5 p-5 space-y-3">
          <div className="text-base font-semibold text-slate-100">Esta página teve um erro</div>
          <div className="text-sm text-slate-300">O resto do BastiON continua funcionando pelo menu. Recarregar costuma resolver. Se repetir, mande um print desta mensagem.</div>
          <pre className="text-xs text-red-300 bg-black/30 rounded p-2 whitespace-pre-wrap break-words max-h-40 overflow-auto">{String(this.state.error?.message || this.state.error)}</pre>
          <div className="flex gap-2">
            <button onClick={this.clearAndReload} className="h-8 px-3 rounded bg-brand hover:bg-brand-strong text-white text-sm">Recarregar</button>
            <button onClick={() => this.setState({ error: null })} className="h-8 px-3 rounded border border-line text-slate-300 text-sm">Tentar de novo</button>
          </div>
        </div>
      </div>
    );
  }
}
