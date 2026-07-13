## Context

qsnap — QEMU/KVM snapshot & backup orchestration tool, аналог btrbk для qcow2/XFS. Проект на стадии готового архитектурного фундамента:

- **Готово:** Core-оркестратор с пайплайном (`_execute_pipeline`), `ConfigFacade` с TOML-парсером и наследованием опций, `TimeBasedRetention` с btrbk-алгоритмом, `SubprocessShell` (обёртка `subprocess.run()`), `JsonStateManager` (атомарная запись JSON в `/var/lib/qsnap/state/`), `DefaultFactory` с DI (принимает `IShell` + `IStateManager`), 9 ABC-интерфейсов, полная mock-инфраструктура.
- **НЕ готово:** `DefaultFactory.create_snapshot_provider`, `create_backup_provider`, `create_change_detector`, `create_lifecycle_manager` — все кидают `NotImplementedError`. Четыре доменных модуля отсутствуют.

**Ключевые архитектурные ограничения (AGENTS.md):**
- Каждый модуль реализует ровно один ABC-интерфейс
- Все зависимости — через конструктор (DI)
- Все fallible-операции возвращают Result-типы, никогда не кидают исключения
- Все virsh/qemu-img вызовы — через `IShell.run()`
- Core — единственный координатор; модули не вызывают друг друга
- Экземпляры создаются через фабрику; нигде нет прямого `SomeModule(...)`

## Goals / Non-Goals

**Goals:**
- Реализовать 4 production-модуля, покрывающих полный жизненный цикл: снэпшот → детект изменений → blockcommit → бэкап
- Заработать `DefaultFactory` на 5/5 методов (сейчас 1/5)
- Обеспечить, что Core-пайплайн вызывает реальные модули, а не только моки
- Покрыть каждый модуль unit-тестами с `MockShell` и contract-тестами интерфейсов
- Использовать эталонные вызовы virsh из `autosnapcommit.sh` как основу для shell-команд

**Non-Goals:**
- CLI-слой — отдельный change
- Поддержка нескольких дисков (vda, vdb, ...) — пока hardcoded `"vda"`
- Lockfile-механизм — отдельный change
- Поддержка `GlobalConfig.timestamp_format` и `preserve_day_of_week` — отдельный change
- Интеграционные/E2E тесты — отдельный change
- Raw backup target (как btrbk raw target) — отдельный change

## Decisions

### D1: Все 4 модуля НЕ наследуют Core (исключение как у TimeBasedRetention)

**Выбор:** `ExternalSnapshotProvider(ISnapshotProvider)`, а не `ExternalSnapshotProvider(Core, ISnapshotProvider)`.

**Обоснование:** `TimeBasedRetention` уже установил прецедент: pure/utility-модули не наследуют Core. Модули snapshot/backup не pure (делают I/O через shell), но их зависимость — только `IShell` (и опционально `IStateManager`). Наследование Core потребовало бы передачи `IConfigFacade`, `IVMModuleFactory`, `IStateManager`, `IShell` в конструктор каждого модуля — избыточно. Фабрика передаёт ровно то, что нужно: `self._shell` и `self._state`.

**Альтернатива отклонена:** Ввести `SnapshotModule(Core)` как промежуточный базовый класс. Это добавило бы слой абстракции без немедленной пользы. Если в будущем модулям понадобится доступ к config/factory из Core — добавим наследование.

### D2: AllocationSizeDetector получает state от фабрики, а не из Core

**Выбор:** Фабрика передаёт `self._state` в конструктор `AllocationSizeDetector(shell, state)`.

**Обоснование:** Детектору нужно знать `last_allocation` (из IStateManager). Core не передаёт state в детектор — он только вызывает `detector.has_changed(vm_config)` и ожидает `ChangeResult`. Детектор сам обращается к state через инжектированную зависимость.

### D3: Shell-команды детектора: domblklist + qemu-img info

**Выбор:** Определять активный образ через `virsh domblklist --domain <vm>`, затем `qemu-img info --output=json --force-share <active_disk>`.

**Обоснование:** После создания снэпшота активный образ меняется (новый qcow2 становится top of chain). `vm_config.base_image` указывает на СТАРЫЙ base, а не на текущий active. `domblklist` всегда возвращает актуальный путь. Эталон: `autosnapcommit.sh:101-106`.

**Альтернатива отклонена:** Использовать `qemu-img info` на `vm_config.base_image` — не будет работать после первого снэпшота.

### D4: Blockcommit — по одному снэпшоту за вызов

**Выбор:** Мерджить снэпшоты по одному: `virsh blockcommit --domain ... --base <base> --top <snap> --delete --wait`. Для нескольких снэпшотов — вызывать последовательно.

**Обоснование:** Безопаснее мерджить по одному: легче откатить при ошибке, понятнее логирование. Для MVP (небольшие цепочки 2-5 снэпшотов) производительность не критична.

**Альтернатива отклонена:** Указать самый дальний `--top` — virsh сам обработает промежуточные. Но тестировать сложнее.

### D5: Бэкап — полное копирование qcow2 + rebase путей

**Выбор:** Копировать qcow2-файлы через `cp`, затем `qemu-img rebase -u -b <new_backing> <target_file>` для перестройки путей к backing-файлам на target.

**Обоснование:** Это единственный способ перенести qcow2-цепочку на другую файловую систему (XFS). `qemu-img rebase -u` меняет ТОЛЬКО метаданные (путь к backing file в заголовке qcow2), не трогая данные — быстро и безопасно.

**Альтернатива отклонена:** Использовать `qemu-img convert` — создаёт НОВЫЙ файл без backing chain (полная копия данных), теряется инкрементальность.

## Risks / Trade-offs

- **[R1] Blockcommit на запущенной ВМ:** В некоторых конфигурациях libvirt/AppArmor блокирует blockcommit на running VM. **Mitigation:** Документировать. Пользователь должен настроить AppArmor-правила. Как fallback: делать blockcommit только когда ВМ shut off (проверять `virsh domstate`).

- **[R2] Инкрементальная цепочка хрупкая:** Потеря промежуточного .qcow2 на target ломает все последующие бэкапы. **Mitigation:** Документировать. В будущем — периодический полный бэкап (full copy без backing chain).

- **[R3] Копирование больших qcow2-файлов:** `cp` копирует ВЕСЬ файл, включая данные, уже присутствующие в backing file. **Mitigation:** Для MVP приемлемо (первые снэпшоты маленькие). В будущем — `qemu-img map` для копирования только allocated-регионов.

- **[R4] Hardcoded disk="vda":** Core жёстко передаёт `disk="vda"` в `provider.create()`. Модуль не может работать с ВМ, у которых диск называется иначе. **Mitigation:** Принято как известное ограничение (Non-Goal).
