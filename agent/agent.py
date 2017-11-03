import numpy as np
from PIL import Image, ImageDraw
from world import Route, World, route_like, Hybrid, save_route, __data__
from net import Willshaw
from visualiser import Visualiser


class Agent(object):
    __latest_agent_id__ = 0
    FOV = (-np.pi/6, 4*np.pi/9)

    def __init__(self, init_pos=np.zeros(3), init_rot=np.zeros(2), condition=Hybrid(),
                 live_sky=True, rgb=False, fov=(-np.pi/2, np.pi/2), visualiser=None, name=None):
        """

        :param init_pos: the initial position
        :type init_pos: np.ndarray
        :param init_rot: the initial orientation
        :type init_rot: np.ndarray
        :param condition:
        :type condition: Hybrid
        :param live_sky: flag to update the sky with respect to the time
        :type live_sky: bool
        :param rgb: flag to set as input to the network all the channels (otherwise use only green)
        :type rgb: bool
        :param fov: vertical field of view of the agent (the widest: -pi/2 to pi/2)
        :type fov: tuple, list, np.ndarray
        :param visualiser:
        :type visualiser: Visualiser
        :param name: a name for the agent
        :type name: basestring
        """
        self.pos = init_pos
        self.rot = init_rot
        self.nest = np.zeros(2)
        self.feeder = np.zeros(2)
        self.live_sky = live_sky
        self.rgb = rgb
        self.visualiser = visualiser

        self.homing_routes = []
        self.world = None  # type: World
        self._net = Willshaw(nb_channels=3 if rgb else 1)  # learning_rate=1)
        self.__is_foraging = False
        self.__is_homing = False
        self.dx = 0.  # type: float
        self.condition = condition
        # self.__per_ground = 1.  # .3  # type: float
        # self.__per_sky = 1.  # .8  # type: float
        self.__per_ground = np.abs(fov[0]) / (np.pi / 2)  # type: float
        self.__per_sky = np.abs(fov[1]) / (np.pi / 2)  # type: float

        Agent.__latest_agent_id__ += 1
        self.id = Agent.__latest_agent_id__
        if name is None:
            self.name = "agent_%02d" % Agent.__latest_agent_id__
        else:
            self.name = name

    def reset(self):
        """
        Resets the agent at the feeder

        :return: a boolean notifying whether the update of the position and orientation is done or not
        """
        self.__is_foraging = False
        self.__is_homing = True
        self._net.update = False

        if len(self.homing_routes) > 0:
            self.pos[:2] = self.feeder.copy()
            self.rot[1] = self.homing_routes[-1].phi[0]
            return True
        else:
            # TODO: warn about the existence of the route
            return False

    def add_homing_route(self, rt):
        """
        Updates the homing route, home and nest points.

        :param rt: The route from the feeder to the nest
        :type rt: Route
        :return: a boolean notifying whether the update is done or not
        """
        if not isinstance(rt, Route):
            return False

        if rt not in self.homing_routes:
            rt.condition = self.condition
            self.homing_routes.append(rt)
            self.nest = np.array(rt.xy[-1])
            self.feeder = np.array(rt.xy[0])
            self.dx = rt.dx
            return True
        return False

    def set_world(self, w):
        """
        Update the world of the agent.

        :param w: the world to be placed in
        :return: a boolean notifying whether the update is done or not
        """
        if not isinstance(w, World):
            return False

        self.world = w
        for rt in self.world.routes:
            self.add_homing_route(rt)
        self.world.routes = self.homing_routes
        return True

    def start_learning_walk(self):
        if self.world is None:
            # TODO: warn about not setting the world
            yield None
            return
        elif len(self.homing_routes) == 0:
            # TODO: warn about not setting the homing route
            yield None
            return

        # initialise visualisation
        if self.visualiser is not None:
            self.visualiser.reset()

        # let the network update its parameters (learn)
        self._net.update = True

        # learn all the available homing routes
        for r in self.homing_routes:
            # add a copy of the current route to the world to visualise the path
            xs, ys, zs, phis = [self.pos[0]], [self.pos[1]], [self.pos[2]], [self.rot[1]]
            self.world.routes.append(
                route_like(r, xs, ys, zs, phis, self.condition, agent_no=self.id, route_no=len(self.world.routes) + 1)
            )
            counter = 0         # count the steps
            pphi = self.rot[1]  # initialise the last orientation to the current

            for x, y, z, phi in r:
                # stop the loop when we close the visualisation window
                if self.visualiser is not None and self.visualiser.is_quit():
                    break

                # update the agent position
                self.pos[:] = x, y, z
                self.rot[1] = phi
                # calculate the distance from the start position (feeder)
                distance = np.sqrt(np.square(self.pos[:2] - self.feeder[:2]).sum())

                # update the route in the world
                xs.append(x)
                ys.append(y)
                zs.append(z)
                phis.append(phi)
                self.world.routes[-1] = route_like(self.world.routes[-1], xs, ys, zs, phis)

                d_phi = np.abs(phi - pphi)

                # generate the visual input and transform it to the projecting neurons
                pn = self.img2pn(self.world_snapshot()[0])
                # make a forward pass from the network (updating the parameters)
                en = self._net(pn)
                counter += 1

                # update view
                img_func = None
                if self.visualiser.mode == "top":
                    img_func = self.world.draw_top_view
                elif self.visualiser.mode == "panorama":
                    img_func = self.world_snapshot
                if self.visualiser is not None:
                    self.visualiser.update_main(img_func,
                                                caption="%s | C: % 2d EN: % 2d Distance: %.2f D_phi: % 2.2f" % (
                                                    self.name, counter, en, distance, np.rad2deg(d_phi)))

                # update last orientation
                pphi = phi

            # remove the copy of the route from the world
            self.world.routes.remove(self.world.routes[-1])
            yield r     # return the learned route

        # freeze the parameters in the network
        self._net.update = False

    def start_homing(self, reset=True):
        if self.world is None:
            # TODO: warn about not setting the world
            return None

        if reset:
            print "Resetting..."
            self.reset()

        # initialise the visualisation
        if self.visualiser is not None:
            self.visualiser.reset()

        # add a copy of the current route to the world to visualise the path
        xs, ys, zs, phis = [self.pos[0]], [self.pos[1]], [self.pos[2]], [self.rot[1]]
        ens = []
        self.world.routes.append(route_like(
            self.world.routes[0], xs, ys, zs, phis, agent_no=self.id, route_no=len(self.world.routes) + 1)
        )

        d_nest = lambda: np.sqrt(np.square(self.pos[:2] - self.nest).sum())
        d_feeder = 0
        counter = 0
        start_time = datetime.now()
        while d_nest() > 0.1:
            x, y, z = self.pos
            phi = self.rot[1]
            # if d_feeder // .1 > counter:
            en, snaps = [], []
            for d_phi in np.linspace(-np.pi / 3, np.pi / 3, 61):
                if self.visualiser is not None and self.visualiser.is_quit():
                    break

                # generate the visual input and transform to the PN values
                snap = self.world_snapshot(d_phi=d_phi)[0]
                snaps.append(snap)
                pn = self.img2pn(snap)

                # make a forward pass from the network
                en.append(self._net(pn))

                if self.visualiser is not None:
                    now = datetime.now() - start_time
                    min = now.seconds // 60
                    sec = now.seconds % 60
                    self.visualiser.update_thumb(snap, pn=self._net.pn, pn_mode="L", caption="Elapsed time: %02d:%02d" % (min, sec))

            if self.visualiser is not None and self.visualiser.is_quit():
                break

            en = np.array(en).flatten()
            ens.append(en)
            # show preference to the least turning angle
            en += np.append(np.linspace(.01, 0., 30, endpoint=False), np.linspace(0., .01, 31))
            phi += np.deg2rad(2 * (en.argmin() - 30))

            counter += 1

            self.rot[1] = phi
            self.pos[:] = x + self.dx * np.cos(phi), y + self.dx * np.sin(phi), z
            xs.append(self.pos[0])
            ys.append(self.pos[1])
            zs.append(self.pos[2])
            phis.append(self.rot[1])

            self.world.routes[-1] = route_like(self.world.routes[-1], xs, ys, zs, phis)

            # update view
            img_func = None
            if self.visualiser.mode == "top":
                img_func = self.world.draw_top_view
            elif self.visualiser.mode == "panorama":
                img_func = self.world_snapshot
            if self.visualiser is not None:
                self.visualiser.update_main(img_func, en=en, thumbs=snaps,
                                            caption="%s | C: % 2d, EN: % 3d (%.2f), D: %.2f, D_nest: %.2f" % (
                                                self.name, counter, 2 * (en.argmin() - 30), en.min(), d_feeder,
                                                d_nest()))

            if d_feeder > 15:
                break
            d_feeder += self.dx
        self.world.routes.remove(self.world.routes[-1])
        np.savez(__data__ + "EN/%s.npz" % self.name, en=np.array(ens))
        return Route(xs, ys, zs, phis, condition=self.condition, agent_no=self.id, route_no=len(self.world.routes) + 1)

    def world_snapshot(self, d_phi=0, width=None, height=None):
        x, y, z = self.pos
        phi = self.rot[1] + d_phi
        img, draw = self.world.draw_panoramic_view(x, y, z, phi, update_sky=self.live_sky,
                                                   include_ground=self.__per_ground, include_sky=self.__per_sky,
                                                   width=width, length=width, height=height)
        return img, draw

    def img2pn(self, image):
        """

        :param image:
        :type image: Image.Image
        :return:
        """
        # TODO: make this parametriseable for different pre-processing of the input
        # print np.array(image).max()
        if self.rgb:
            return np.array(image).flatten()
        else:  # keep only green channel
            return np.array(image).reshape((-1, 3))[:, 1].flatten()


if __name__ == "__main__":
    from world import load_world, load_routes
    from datetime import datetime
    from utils import *

    date = datetime.now()
    update_sky = True
    uniform_sky = False
    enable_pol = False
    rgb = False
    fov = (-np.pi/2, np.pi/2)
    sky_type = "uniform" if uniform_sky else "live" if update_sky else "fixed"
    if not enable_pol:
        sky_type += "-no-pol"
    if rgb:
        sky_type += "-rgb"
    step = .1       # 10 cm
    tau_phi = np.pi    # 60 deg
    condition = Hybrid(tau_x=step, tau_phi=tau_phi)
    agent_name = create_agent_name(date, sky_type, step, fov[0], fov[1])
    print agent_name

    world = load_world()
    world.enable_pol_filters(enable_pol)
    world.uniform_sky = uniform_sky
    routes = load_routes()
    world.add_route(routes[0])

    agent = Agent(condition=condition, live_sky=update_sky, visualiser=Visualiser(), rgb=rgb, fov=fov, name=agent_name)
    agent.set_world(world)
    print agent.homing_routes[0]

    agent.visualiser.set_mode("panorama")
    for route in agent.start_learning_walk():
        print "Learned route:", route
        if route is not None:
            save_route(route, "learned-%d-%d-%s" % (route.agent_no, route.route_no, agent_name))

    agent.visualiser.set_mode("top")
    route = agent.start_homing()
    print route
    if route is not None:
        save_route(route, "homing-%d-%d-%s" % (route.agent_no, route.route_no, agent_name))

    update_tests(sky_type, date, step, gfov=fov[0], sfov=fov[1])
    agent.world.routes.append(route)
    img, _ = agent.world.draw_top_view(1000, 1000)
    img.save(__data__ + "routes-img/%s.png" % agent_name, "PNG")
    # img.show(title="Testing route")
