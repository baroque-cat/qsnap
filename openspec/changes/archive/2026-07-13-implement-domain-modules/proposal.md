## Why

Проект qsnap имеет полный архитектурный фундамент: все 9 ABC-интерфейсов, Core-оркестратор с пайплайном, retention-движок, конфигурационный слой, state-менеджер и shell-абстракцию реализованы и покрыты тестами. Но `DefaultFactory` имеет 4 из 5 методов, кидающих `NotImplementedError` — система не может создать ни одного снэпшота, бэкапа, определить изменения или выполнить blockcommit. Без этих четырёх доменных модулей qsnap не способен выполнить ни одной полезной операции в production-режиме.

## What Changes

- **Новый модуль** `ExternalSnapshotProvider` — создание внешних disk-only снэпшотов через `virsh snapshot-create-as`
- **Новый модуль** `AllocationSizeDetector` — детект изменений диска ВМ через `qemu-img info` (режим `onchange`)
- **Новый модуль** `BlockCommitManager` — управление жизненным циклом backing chain через `virsh blockcommit`
- **Новый модуль** `FileCopyBackupProvider` — инкрементальное копирование снэпшотов на внешний диск (XFS) с `qemu-img rebase`
- **Регистрация** всех четырёх модулей в `DefaultFactory` (методы `create_snapshot_provider`, `create_change_detector`, `create_lifecycle_manager`, `create_backup_provider` перестают кидать `NotImplementedError`)
- **Тесты** для каждого модуля с `MockShell`, contract-тесты интерфейсов, фикстуры shell-выводов

## Capabilities

### New Capabilities

- `snapshot-provider`: создание, листинг и удаление внешних qcow2-снэпшотов через virsh
- `change-detection`: определение изменений диска ВМ по allocation-size через qemu-img info
- `lifecycle-manager`: blockcommit старых снэпшотов для сокращения backing chain
- `backup-provider`: копирование снэпшотов на внешнее хранилище с перестройкой backing-путей

### Modified Capabilities

*Нет изменяемых существующих спецификаций.*

## Impact

- `qsnap/modules/` — новая директория с поддиректориями `snapshot/`, `change/`, `lifecycle/`, `backup/`
- `qsnap/factory/default.py` — 4 метода заменяют `raise NotImplementedError` на return конкретных классов
- `tests/modules/` — новые тестовые модули в `snapshot/`, `change/`, `lifecycle/`, `backup/`
- `tests/interfaces/` — новые contract-тесты для четырёх интерфейсов
- `tests/fixtures/shell_outputs/` — новые фикстуры с эталонными выводами virsh/qemu-img
- Зависимости: без изменений (только stdlib)
