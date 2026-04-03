#!/usr/bin/env node

const fs = require("fs");
const path = require("path");

function parseArgs(argv) {
  const parsed = {};
  for (let index = 2; index < argv.length; index += 1) {
    const token = argv[index];
    const next = argv[index + 1];
    if (!token.startsWith("--") || next == null) {
      continue;
    }
    parsed[token.slice(2)] = next;
    index += 1;
  }
  return parsed;
}

function output(payload) {
  process.stdout.write(`${JSON.stringify(payload)}\n`);
}

function positionInfo(sourceFile, node) {
  if (typeof sourceFile.getLineAndCharacterOfPosition === "function" && typeof node.getStart === "function") {
    const start = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile, false));
    const end = sourceFile.getLineAndCharacterOfPosition(node.end ?? node.getEnd?.() ?? node.getStart(sourceFile, false));
    return {
      lineStart: start.line + 1,
      lineEnd: end.line + 1,
    };
  }
  return {
    lineStart: 1,
    lineEnd: 1,
  };
}

function symbolsFromSource(ts, sourceFile) {
  const symbols = [];
  const addSymbol = (name, kind, node) => {
    if (!name || symbols.some((item) => item.title === name)) {
      return;
    }
    const position = positionInfo(sourceFile, node);
    symbols.push({
      title: name,
      kind,
      line_start: position.lineStart,
      line_end: position.lineEnd,
    });
  };

  const statements = Array.isArray(sourceFile.statements) ? sourceFile.statements : [];
  for (const statement of statements) {
    if ((ts.isFunctionDeclaration && ts.isFunctionDeclaration(statement)) || statement.kind === ts.SyntaxKind?.FunctionDeclaration) {
      addSymbol(statement.name?.text, "function", statement);
      continue;
    }
    if ((ts.isClassDeclaration && ts.isClassDeclaration(statement)) || statement.kind === ts.SyntaxKind?.ClassDeclaration) {
      addSymbol(statement.name?.text, "class", statement);
      continue;
    }
    if ((ts.isInterfaceDeclaration && ts.isInterfaceDeclaration(statement)) || statement.kind === ts.SyntaxKind?.InterfaceDeclaration) {
      addSymbol(statement.name?.text, "interface", statement);
      continue;
    }
    if ((ts.isTypeAliasDeclaration && ts.isTypeAliasDeclaration(statement)) || statement.kind === ts.SyntaxKind?.TypeAliasDeclaration) {
      addSymbol(statement.name?.text, "type", statement);
      continue;
    }
    if ((ts.isEnumDeclaration && ts.isEnumDeclaration(statement)) || statement.kind === ts.SyntaxKind?.EnumDeclaration) {
      addSymbol(statement.name?.text, "enum", statement);
      continue;
    }
    if ((ts.isVariableStatement && ts.isVariableStatement(statement)) || statement.kind === ts.SyntaxKind?.VariableStatement) {
      const declarations = statement.declarationList?.declarations || [];
      for (const declaration of declarations) {
        addSymbol(declaration.name?.text, "constant", declaration);
      }
    }
  }
  return symbols.slice(0, 12);
}

function dependenciesFromSource(ts, sourceFile) {
  const dependencies = [];
  const statements = Array.isArray(sourceFile.statements) ? sourceFile.statements : [];
  for (const statement of statements) {
    if ((ts.isImportDeclaration && ts.isImportDeclaration(statement)) || statement.kind === ts.SyntaxKind?.ImportDeclaration) {
      const value = statement.moduleSpecifier?.text;
      if (value && !dependencies.includes(value)) {
        dependencies.push(value);
      }
      continue;
    }
    if ((ts.isExportDeclaration && ts.isExportDeclaration(statement)) || statement.kind === ts.SyntaxKind?.ExportDeclaration) {
      const value = statement.moduleSpecifier?.text;
      if (value && !dependencies.includes(value)) {
        dependencies.push(value);
      }
    }
  }
  return dependencies.slice(0, 16);
}

function main() {
  const args = parseArgs(process.argv);
  if (!args.file || !args["typescript-module"]) {
    output({ status: "error", reason: "missing_arguments" });
    process.exit(1);
  }

  const sourceText = fs.readFileSync(args.file, "utf8");
  const typescriptModule = require(path.resolve(args["typescript-module"]));

  if (typeof typescriptModule.agentiuxExtract === "function") {
    const payload = typescriptModule.agentiuxExtract(sourceText, args.file);
    output({
      status: "ok",
      backend: "typescript_compiler",
      symbols: Array.isArray(payload?.symbols) ? payload.symbols : [],
      dependencies: Array.isArray(payload?.dependencies) ? payload.dependencies : [],
    });
    return;
  }

  if (typeof typescriptModule.createSourceFile !== "function") {
    output({ status: "error", reason: "missing_create_source_file" });
    process.exit(1);
  }

  const sourceFile = typescriptModule.createSourceFile(
    args.file,
    sourceText,
    typescriptModule.ScriptTarget?.Latest ?? 99,
    true
  );
  output({
    status: "ok",
    backend: "typescript_compiler",
    symbols: symbolsFromSource(typescriptModule, sourceFile),
    dependencies: dependenciesFromSource(typescriptModule, sourceFile),
  });
}

main();
