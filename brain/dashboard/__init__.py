"""O dashboard web da Juniper (ADR-020): somente leitura, dentro do processo do servidor.

Uma superfície nova do cérebro, para ver a memória sem o terminal. Fala HTTP (GET) na tailnet,
autentica com o token do ADR-007 (que vira um cookie assinado) e entrega JSON e arquivos
estáticos. O navegador nunca fala com o Mongo e nada aqui escreve no banco. O núcleo (`brain/core`)
não conhece este pacote, e uma falha dele nunca derruba o servidor nem a conversa.
"""
