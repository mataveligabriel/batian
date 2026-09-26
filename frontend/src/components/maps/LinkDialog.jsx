import React, { useState } from "react";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { InterfacePicker } from "@/components/maps/InterfacePicker";
import { fmtSpeed } from "@/lib/netfmt";

function Side({ node, value, onPick, letter, nameOf }) {
  return (
    <div className="flex-1 min-w-0">
      <div className="text-xs text-slate-300 mb-1.5 flex items-center justify-between">
        <span><b className="text-[#4DA3FF]">{letter}</b> · {nameOf(node)}</span>
        <span className="font-mono text-[11px] text-slate-400">{value ? `${value.name} (${fmtSpeed(value.speed_mbps)})` : "nenhuma"}</span>
      </div>
      <InterfacePicker deviceId={node?.device_id} value={value?.index} onPick={onPick} height="h-64" />
    </div>
  );
}

/** Cria/edita um link: interface de cada lado (pelo menos uma), capacidade e nome. */
export function LinkDialog({ open, link, nodeA, nodeB, nameOf, onCancel, onSave }) {
  const [fromIf, setFromIf] = useState(link?.from_if || null);
  const [toIf, setToIf] = useState(link?.to_if || null);
  const [cap, setCap] = useState(link?.capacity_mbps || "");
  const [label, setLabel] = useState(link?.label || "");
  if (!open) return null;
  const autoCap = fromIf?.speed_mbps || toIf?.speed_mbps || null;
  const pick = (setter) => (i) => setter(prev => (prev?.index === i.index ? null : { index: i.index, name: i.name, speed_mbps: i.speed_mbps || null }));


  return (
    <Dialog open={open} onOpenChange={(v) => !v && onCancel()}>
      <DialogContent className="bg-[#111722] border-[#1E293B] text-slate-100 max-w-4xl max-h-[92vh] overflow-y-auto" data-testid="link-dialog">
        <DialogHeader><DialogTitle>{link?.id ? "Editar link" : "Novo link"}: {nameOf(nodeA)} ↔ {nameOf(nodeB)}</DialogTitle></DialogHeader>
        <p className="text-xs text-slate-400">
          Escolha a interface em <b>pelo menos um</b> dos lados — o tráfego é lido dela por SNMP. Se escolher dos dois lados,
          o Bastion usa o lado A e o B serve de reserva e para o alarme de queda.
        </p>
        <div className="flex gap-4 flex-col md:flex-row">
          <Side node={nodeA} value={fromIf} onPick={pick(setFromIf)} letter="A" nameOf={nameOf} />
          <Side node={nodeB} value={toIf} onPick={pick(setToIf)} letter="B" nameOf={nameOf} />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label>Capacidade do link (Mbps)</Label>
            <Input type="number" min={1} value={cap} onChange={e => setCap(e.target.value)} data-testid="link-capacity"
                   placeholder={autoCap ? `automático: ${autoCap} (velocidade da porta)` : "ex.: 10000"} className="bg-[#05070A] border-[#1E293B] font-mono" />
            <div className="text-[11px] text-slate-500 mt-1">Use quando o contrato é menor que a porta (ex.: 2 Gbps numa porta de 10G).</div>
          </div>
          <div>
            <Label>Nome do link (opcional)</Label>
            <Input value={label} onChange={e => setLabel(e.target.value)} placeholder="ex.: Trânsito IP · Operadora X" data-testid="link-label"
                   className="bg-[#05070A] border-[#1E293B]" />
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onCancel}>Cancelar</Button>
          <Button onClick={() => onSave({ from_if: fromIf, to_if: toIf, capacity_mbps: cap ? Number(cap) : null, label: label.trim() })}
                  data-testid="link-save" className="bg-[#007AFF] hover:bg-[#0062CC]">Salvar link</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
