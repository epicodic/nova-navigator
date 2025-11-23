import threading

from nova_navigator.filemanager.tasks import _iterate_files
from nova_navigator.task import TaskStatus
from nova_navigator.vfs2.filesystems.local import LocalFilesystem
from tests.test_utils import DATA_DIR


def test_iterate_files() -> None:
    fs = LocalFilesystem.singleton()
    data_dir_path = fs.path(DATA_DIR / "local")

    def progress_callback(status: TaskStatus) -> None:
        print(f"Progress: {status.progress.completed}/{status.progress.total}")

    status = TaskStatus(threading.Event(), progress_callback=progress_callback)

    for path in _iterate_files(status, data_dir_path):
        print(f"Found file: {path.path}")


# @pytest.mark.asyncio
# async def test_remove_non_empty_directory():
#     async def gui_callback(
#         _request: DecisionRequest,
#         future: asyncio.Future[DecisionResponse],
#     ) -> None:
#         # Simulate user interaction delay
#         await asyncio.sleep(0.1)
#         future.set_result(DecisionResponse.YES_TO_ALL)

#     def progress_callback(_: TaskStatus) -> None:
#         pass

#     gui_mock = AsyncMock(wraps=gui_callback)

#     status = TaskStatus(threading.Event(), progress_callback=progress_callback)

#     fs = LocalFilesystem.singleton()

#     with temporary_directory() as tmp_dir:
#         dir_path = tmp_dir / "non_empty_dir"
#         os.makedirs(dir_path, exist_ok=True)
#         create_file(dir_path / "file1.txt", "data1")

#         tasks = [
#             erase(
#                 status=status,
#                 paths=[fs.path(dir_path)],
#             )
#         ]

#         await TaskScheduler.execute(gui_request_callback=gui_mock, tasks=tasks)
