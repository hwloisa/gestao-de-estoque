# Controle de Estoque · Creche Carolina Guilhermina Schnoor Bohianza

Aplicação web responsiva para controlar o estoque de materiais pedagógicos e de limpeza da creche.

## Funcionalidades

- **Painel geral** com itens cadastrados, total em estoque, itens em falta, itens abaixo da previsão mensal, resumo de entradas e saídas do mês e histórico recente.
- **Alertas de reposição**: todo item com estoque menor que a previsão mensal fica destacado em vermelho, no painel e na listagem.
- **Cadastro de itens**: nome, categoria, unidade de medida, previsão mensal e saldo inicial opcional. As categorias são fixas: **Pedagógica** e **Limpeza** — não é preciso criar categoria antes de cadastrar um item.
- **Unidades de medida**: Unidade, Caixa, Saco, Fardo, Resma, Litro, Par, Maço, Pacote e Frasco.
- **Relatórios em Excel**: prévia do estoque por setor, exportação no formato oficial da prefeitura, modelo de planilha para download e importação de planilhas `.xlsx`.
- **Registro de movimentações** com botões rápidos **+ / −** (1 unidade por toque), filtro rápido por setor, busca e registro detalhado com quantidade e observação.
- **Acesso protegido por login**: nenhuma página ou alteração fica disponível sem autenticação.
- Layout responsivo (desktop, tablet e celular).

## Relatórios em Excel

A aba **Relatórios** concentra a troca de dados com a prefeitura.

### Formato oficial da exportação

Cada aba do arquivo representa um setor e segue exatamente este layout:

| Linha | Conteúdo |
| --- | --- |
| 1 | `PREFEITURA MUNICIPAL DE LIMEIRA - SECRETARIA DE EDUCAÇÃO` |
| 2 | `MATERIAIS DE LIMPEZA` ou `MATERIAIS PEDAGÓGICOS` + `MÊS: NOVEMBRO` |
| 3 | `ESCOLA: CI "Carolina Guilhermina Schnoor Bohianza"` |
| 4 | `N` · `MATERIAIS` · `COMO SOLICITAR (UNIDADE)` · `PREVISAO MENSAL` · `ESTOQUE` |
| 5 em diante | Número do item (1 até o total), nome do material, unidade, previsão mensal e estoque |

- Com um setor selecionado na tela, o arquivo tem uma única aba.
- Com o filtro **Todos**, o arquivo tem uma aba por setor. Se um setor estiver sem itens, a aba dele fica vazia e a aba com dados vem primeiro.
- Se não houver nenhum item, a exportação é recusada com uma mensagem clara em vez de gerar um arquivo vazio.
- **Baixar modelo** entrega o mesmo layout com exemplos e uma aba **Instruções**.

### Importação

- A importação identifica os itens pelo **nome do material**, nunca pelo número da coluna **N** (ou **ITEM**).
- O cabeçalho da planilha oficial (`ITEM` · `MATERIAIS` · `COMO SOLICITAR` · `PREVISÃO MENSAL` · `ESTOQUE`) é reconhecido automaticamente, inclusive com as células mescladas.
- Itens com o mesmo nome no mesmo setor são **atualizados**; itens novos são **cadastrados**. O resultado mostra a lista com os nomes importados e atualizados.
- O setor é identificado pela aba, pelo título ou, se necessário, pelo campo **Setor padrão** da tela.
- A diferença de quantidade gera uma movimentação de entrada ou saída com a observação “Importação de planilha”; itens novos com saldo recebem uma entrada de “Saldo inicial (importação)”.
- Unidades aceitas: **Unidade, Caixa, Saco, Fardo, Resma, Litro, Par, Maço, Pacote e Frasco**.
  - Variações como `caixa c/ 24`, `caixa com 120`, `unidade de 10 em 10`, `pacote c/ 100` e `resma (pequena)` usam a unidade base correspondente.
  - Uma unidade não reconhecida (por exemplo, `rolo`) não descarta o item: ele é importado como **Unidade** e aparece como aviso no resultado.
- Limite de 1000 linhas por arquivo, somando todas as abas. Linhas inválidas são ignoradas e listadas no resultado da importação.
- O formato antigo (com coluna **Setor**) continua aceito.
- A exportação e a importação são protegidas por login e CSRF, como o restante do sistema.

## Onde ficam os dados

O banco SQLite fica em uma pasta local, **fora do OneDrive**:

```
%LOCALAPPDATA%\GestaoDeEstoque\estoque.db
```

Isso evita que a sincronização do OneDrive substitua o banco por uma versão antiga e faça os itens importados desaparecerem.

- Na primeira execução após esta mudança, o `estoque.db` que estiver na pasta do projeto é copiado automaticamente para a pasta local.
- Para fazer backup, pare o sistema e copie o arquivo `estoque.db` da pasta local.
- Para usar outro local, defina a variável de ambiente `ESTOQUE_DB_PATH` com o caminho completo do banco.

## Acesso e segurança

O sistema exige login para abrir o painel e para qualquer alteração.

- **Primeiro acesso**: enquanto não existir nenhuma conta, a página inicial abre o modo *Primeiro acesso*. Informe o nome do responsável, um usuário e uma senha (mínimo de 8 caracteres). Essa conta é administradora e só pode ser criada uma vez.
- **Sessão**: após entrar, a sessão permanece ativa por 12 horas. Use o menu do usuário no canto superior direito para sair.
- **Proteção das alterações**: toda requisição de escrita (POST, PUT, DELETE) exige o token CSRF da sessão, o que impede alterações vindas de páginas externas.
- **Tentativas de login**: após 5 senhas erradas em 5 minutos, o acesso daquele usuário fica bloqueado por 2 minutos.
- **Senhas**: ficam no banco apenas como hash (Werkzeug), nunca em texto puro.

Se precisar recriar a conta de acesso, pare o servidor e apague os usuários do banco:

```powershell
.venv\Scripts\python.exe -c "import sqlite3, app; c=sqlite3.connect(app.DATABASE_PATH); c.execute('DELETE FROM users'); c.commit(); c.close()"
```

Na próxima abertura, o modo de primeiro acesso volta a aparecer.

### Variáveis de ambiente (opcionais)

| Variável | Para que serve |
| --- | --- |
| `SECRET_KEY` | Chave que assina o cookie de sessão. Se não for definida, o sistema gera e guarda uma em `.secret_key`. |
| `SESSION_COOKIE_SECURE=1` | Envia o cookie de sessão apenas por HTTPS. **Ative em produção.** |
| `ESTOQUE_DB_PATH` | Caminho completo de outro arquivo SQLite. Útil para testes e QA sem afetar os dados reais. |
| `FLASK_DEBUG=1` | Ativa o modo debug do Flask. Use apenas em desenvolvimento local. |
| `PORT` | Porta do servidor local. Padrão: `5000`. |

> `.secret_key` e o banco de dados local não devem ser versionados nem compartilhados: quem tiver a chave ou o banco pode comprometer as sessões.

### Em produção

O servidor embutido do Flask (`app.run`) serve apenas para desenvolvimento. Para uso em rede ou produção:

- publique atrás de HTTPS (proxy reverso como Nginx ou Caddy);
- defina `SECRET_KEY` e `SESSION_COOKIE_SECURE=1`;
- use um servidor WSGI (por exemplo, `waitress` no Windows ou `gunicorn` no Linux);
- restrinja o acesso à rede local da creche quando possível.

## Tecnologias

- Frontend: **HTML5, CSS3 e JavaScript puro** (sem bibliotecas).
- Backend: **Python 3 + Flask**.
- Banco de dados: **SQLite** (criado automaticamente na pasta local do usuário).
- Planilhas: **openpyxl** para gerar e ler arquivos `.xlsx` (já incluído em `requirements.txt`).

## Como executar

No Windows (PowerShell), dentro da pasta do projeto:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

No Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Abra o navegador em <http://127.0.0.1:5000>. Na primeira vez, o sistema pede a criação da conta de administrador (veja [Acesso e segurança](#acesso-e-segurança)).

> Na primeira execução, o banco é criado com as duas categorias fixas e itens de exemplo para facilitar a visualização. Para começar do zero, pare o servidor, apague o banco da pasta local (ou defina `ESTOQUE_DB_PATH` para um arquivo novo) e inicie novamente — as categorias e os itens de exemplo serão recriados. Para manter um banco vazio, remova a chamada `seed_database(connection)` em `init_db()` no `app.py`.
>
> Bancos criados por versões anteriores são ajustados automaticamente na inicialização: os itens são movidos para a categoria fixa do seu setor e as categorias extras são removidas, mantendo apenas **Pedagógica** e **Limpeza**.

## Estrutura do projeto

```
gestao-de-estoque/
├── app.py                 # Flask, API REST e inicialização do SQLite
├── requirements.txt       # dependências de execução
├── .gitignore             # banco, segredos e caches fora do Git
├── templates/
│   ├── index.html         # interface (SPA)
│   └── login.html         # acesso e primeiro acesso
└── static/
    ├── css/style.css      # estilos e responsividade
    └── js/
        ├── app.js         # lógica do frontend
        └── login.js       # login e criação da primeira conta
```

O banco `estoque.db`, a chave `.secret_key` e as exportações locais são criados em tempo de execução e **não** entram no repositório.

## Endpoints principais

Exceto `/api/login`, `/api/setup` e os arquivos estáticos, todas as rotas exigem sessão autenticada. Requisições de escrita também exigem o cabeçalho `X-CSRF-Token`.

| Método | Rota | Descrição |
| --- | --- | --- |
| POST | `/api/login` | Autenticar (público) |
| POST | `/api/setup` | Criar a primeira conta (só enquanto não houver usuários) |
| POST | `/api/logout` | Encerrar a sessão |
| GET | `/api/bootstrap` | Itens, categorias, painel, unidades e setores |
| GET | `/api/dashboard` | Resumo do painel |
| GET/POST | `/api/items` | Listar / cadastrar itens |
| PUT/DELETE | `/api/items/<id>` | Editar / excluir item |
| GET | `/api/categories` | Listar as duas categorias fixas |
| GET/POST | `/api/movements` | Histórico / registrar entrada ou saída |
| GET | `/api/reports/export` | Exportar estoque em `.xlsx` (aceita `?sector=`) |
| GET | `/api/reports/template` | Baixar o modelo de planilha |
| POST | `/api/reports/import` | Importar planilha `.xlsx` |

O cadastro de uma saída valida o estoque disponível. As categorias são fixas e não podem ser criadas, editadas ou excluídas pela interface.

## Publicar no GitHub

O repositório já está preparado para publicação: o `.gitignore` mantém banco, chave de sessão, caches e exportações fora do Git.

1. Configure sua identidade no Git (uma única vez):

```powershell
git config --global user.name "Seu Nome"
git config --global user.email "seu-email@exemplo.com"
```

2. Crie um repositório **vazio** em <https://github.com/new>. Não marque README, `.gitignore` nem licença.

3. Envie o projeto:

```powershell
git remote add origin https://github.com/SEU-USUARIO/gestao-de-estoque.git
git push -u origin main
```

Na primeira vez, o Git Credential Manager abre o navegador para autenticar. Se a conta usar verificação em duas etapas, use um **Personal Access Token** no lugar da senha. Também é possível usar SSH.

4. Para dar acesso ao QA, use **Settings → Collaborators** e envie o link do repositório. Ele poderá clonar, executar e validar o sistema.

> Evite manter o repositório Git dentro do OneDrive. A sincronização pode conflitar com a pasta `.git`. Se possível, mova o projeto para uma pasta local, como `C:\dev\gestao-de-estoque`, antes de continuar usando o Git.
