# Bastion SSH — app para Windows

Janela própria para o Bastion. **Não roda nada no Windows**: abre o servidor Bastion (Linux) pelo
endereço configurado. Atualizou o servidor → fechou e abriu o app → já está na versão nova
(o cache é limpo a cada abertura; login, preferências e abas continuam).

## Gerar o instalador (sem instalar nada no PC)
1. GitHub → **Actions** → **App Windows (Bastion SSH)** → **Run workflow**.
2. Espere ~5 min, abra a execução concluída e baixe **Bastion-SSH-Windows** em *Artifacts*.
3. Dentro do zip:
   - `Bastion-SSH-Setup-x.y.z.exe` — instalador (sem admin): atalho na área de trabalho e no menu Iniciar.
   - `Bastion-SSH-Portatil-x.y.z.exe` — roda sem instalar.

O app só precisa ser gerado de novo se mudar algo **nesta pasta** (ícone, menu…). Atualizações do
sistema chegam sozinhas pelo servidor.

## Uso
- 1ª abertura: informe o endereço do servidor (ex.: `177.184.221.5` ou `https://bastion.suaempresa.com`).
- Menu (tecla **Alt**): **Bastion → Alterar servidor**, **F5** recarregar, **Ctrl+Shift+R** recarregar sem cache,
  **F11** tela cheia, **Ctrl + / Ctrl -** zoom.
- Terminal: selecionar copia e botão direito cola também funcionam com o servidor em `http://IP`.
- Servidor fora do ar → tela de erro que tenta reconectar sozinha a cada 15 s.

## Gerar localmente (opcional)
Com Node.js 20+ no Windows: `cd desktop && npm install && npm run dist` → instaladores em `desktop/dist/`.
